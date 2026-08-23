"""Playback transport for video files (play/pause/stop, seek, trim, loop, speed).

The camera path has no notion of playback: frames arrive when the sensor
produces them. A file, in contrast, is a *seekable* source, so it needs a
transport of its own. This module hosts that transport as a single worker
thread whose loop is driven by three pieces of shared state, all written by the
GUI thread and read by the worker:

  * a pause ``Event``, so a pause blocks the decode loop instead of spinning;
  * a ``_pending_seek`` slot, so a seek can be serviced *while paused* (that is
    what makes the timeline scrubbable when playback is stopped); and
  * plain attributes for in/out trim, loop and playback FPS, which are single
    machine words in CPython and therefore safe to publish without a lock.

The pacing deliberately never "sprints" to make up for a late frame: falling
behind once must not turn into a burst of frames that looks like fast-forward.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Optional

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from structures import ThreadStatisticsData

# Pacing sleeps are chopped into slices of this length so that a stop request
# or a scrub is observed promptly instead of after a whole frame interval
# (which, at a low manual playback rate, can be seconds long).
_SLEEP_SLICE = 0.020

# Fallback rate for containers that do not report a usable FPS.
_DEFAULT_FPS = 30.0


class VideoFileWorker(QThread):
    """Decodes a video file under an explicit transport state machine.

    Unlike the camera worker, this one is *never* torn down by ``stop()``:
    stopping only rewinds and pauses, so the timeline stays interactive and the
    user can keep scrubbing a stopped clip. Only :meth:`shutdown` ends the
    thread.
    """

    stats = pyqtSignal(object)
    capture_fps = pyqtSignal(float)
    #: Emitted after every decoded frame with the frame index just published,
    #: so the timeline playhead can follow playback without polling OpenCV.
    position_changed = pyqtSignal(int)
    #: Emitted once the container has been probed (frame count, native FPS).
    opened = pyqtSignal(int, float)
    #: Emitted when playback state changes, so transport buttons can re-label.
    playing_changed = pyqtSignal(bool)

    def __init__(
        self,
        path: str,
        frame_queue: "queue.Queue",
        drop_if_full: bool,
    ) -> None:
        super().__init__()
        self._path = path
        self._queue = frame_queue
        self._drop = drop_if_full
        self._stop = False

        # Pause gate. Set == running; cleared == paused. A file starts parked so
        # the first frame is only decoded once the user (or the tab) hits play.
        self._resume = threading.Event()
        # ``_resume`` doubles as the scrub wake-up, so it cannot by itself tell
        # "the user pressed play" from "wake up once and render a scrub frame".
        # This flag carries the user's intent and is the loop's real authority.
        self._playing_intent = False

        self._pending_seek: Optional[int] = None
        self._seek_lock = threading.Lock()
        # Bumped on every seek request; the worker compares the token it acted
        # on with the current one to discard a scrub frame already superseded.
        self._seek_token = 0

        self._loop = False
        self._in_frame = 0
        self._out_frame = -1  # exclusive; -1 means "to the end"
        self._frame_count = 0
        self._reported_fps = _DEFAULT_FPS
        self._playback_fps = 0.0  # 0 == follow the container's native rate
        self._at_end = False
        self._pos = 0  # index of the NEXT frame to decode (worker thread only)

        # Deadline-based pacing state (worker thread only).
        self._next_deadline = 0.0
        self._pacing_valid = False

    # ------------------------------------------------------------------
    # Transport API (GUI thread)
    # ------------------------------------------------------------------

    def play(self) -> None:
        """Resume playback, restarting from the in-point if parked at the end."""
        # Set the intent BEFORE any seek: seek_frame() opens the pause gate, and
        # a worker waking on it must already see "playing" or it would re-park.
        self._playing_intent = True
        if self._at_end:
            self.seek_frame(self._in_frame)
        self._resume.set()
        self.playing_changed.emit(True)

    def pause(self) -> None:
        """Suspend decoding without losing position."""
        self._playing_intent = False
        self._resume.clear()
        self.playing_changed.emit(False)

    def stop(self) -> None:
        """Rewind to the in-point and pause, keeping the thread alive.

        A file source is seekable, so "stop" must not destroy the worker: doing
        so would leave the timeline dead and force a costly reopen on the next
        play. Rewind-and-park gives the same user-visible effect while keeping
        the clip scrubbable.
        """
        self._playing_intent = False
        self._resume.clear()
        self.seek_frame(self._in_frame)
        self.playing_changed.emit(False)

    def is_playing(self) -> bool:
        return self._playing_intent and not self._at_end

    def seek_frame(self, frame: int) -> None:
        """Request a jump to ``frame``, clamped into the active in/out range.

        Publishing the request instead of seeking inline keeps every
        ``VideoCapture`` call on the worker thread (OpenCV captures are not
        thread-safe), and waking the pause gate is what allows a scrub to
        render a preview frame while paused.
        """
        hi = max(self._in_frame, self._effective_out() - 1)
        target = min(max(int(frame), self._in_frame), hi)
        with self._seek_lock:
            self._pending_seek = target
            self._seek_token += 1
        self._at_end = False
        # Wake a paused worker for exactly one frame so the scrub is visible.
        self._resume.set()

    def set_in_out(self, in_frame: int, out_frame: int) -> None:
        """Restrict playback to ``[in, out)``; ``out < 0`` means "to the end"."""
        total = self._frame_count if self._frame_count > 0 else (1 << 62)
        out = -1 if out_frame < 0 else min(max(int(out_frame), 1), total)
        hi = (total if out < 0 else out) - 1
        self._in_frame = min(max(int(in_frame), 0), max(0, hi))
        self._out_frame = out

    def set_loop(self, enabled: bool) -> None:
        """Enable or disable wrap-around at the out-point (default: disabled)."""
        self._loop = bool(enabled)

    def set_playback_fps(self, fps: float) -> None:
        """Override the emission cadence; ``<= 0`` restores the native rate."""
        self._playback_fps = float(fps) if fps and fps > 0 else 0.0

    def playback_fps(self) -> float:
        """The cadence actually used for pacing."""
        return self._playback_fps if self._playback_fps > 0 else self._reported_fps

    def reported_fps(self) -> float:
        return self._reported_fps

    def frame_count(self) -> int:
        return self._frame_count

    def current_frame(self) -> int:
        return max(0, self._pos - 1)

    def seekable(self) -> bool:
        """A container with no usable frame count cannot drive a timeline."""
        return self._frame_count > 0

    def at_end(self) -> bool:
        return self._at_end

    def shutdown(self) -> None:
        """Terminate the worker for good (the only call that ends the thread)."""
        self._stop = True
        self._resume.set()  # release a paused worker so it can observe the flag

    # ------------------------------------------------------------------
    # Internals (worker thread)
    # ------------------------------------------------------------------

    def _effective_out(self) -> int:
        if self._out_frame >= 0:
            return self._out_frame
        return self._frame_count if self._frame_count > 0 else (1 << 62)

    def _reset_pacing(self) -> None:
        """Forget the accumulated deadline after a discontinuity.

        Without this, the time spent paused or seeking would be counted as
        "lateness" and the next frames would be released back to back.
        """
        self._pacing_valid = False

    def _pace(self) -> None:
        """Sleep until the next frame's deadline, never sprinting to catch up."""
        interval = 1.0 / max(0.1, self.playback_fps())
        now = time.perf_counter()
        if not self._pacing_valid:
            self._next_deadline = now
            self._pacing_valid = True
        self._next_deadline += interval
        if now >= self._next_deadline:
            # Behind schedule: drop the deficit and re-base the cadence on now,
            # so a hiccup never turns into a burst of frames.
            self._next_deadline = time.perf_counter()
            return
        while not self._stop:
            now = time.perf_counter()
            if now >= self._next_deadline:
                break
            time.sleep(min(self._next_deadline - now, _SLEEP_SLICE))

    def _take_seek(self) -> tuple[Optional[int], int]:
        with self._seek_lock:
            target, self._pending_seek = self._pending_seek, None
            return target, self._seek_token

    def _seek_superseded(self, token: int) -> bool:
        with self._seek_lock:
            return self._pending_seek is not None and self._seek_token != token

    def _publish(self, frame, emitted: int, paced: bool) -> None:
        """Hand a decoded frame to the processing queue and update the UI."""
        if self._drop and self._queue.full():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
        if paced:
            # Pace before enqueueing so back-pressure and cadence compose
            # instead of fighting each other.
            self._pace()
        try:
            self._queue.put(frame, timeout=0.5)
        except queue.Full:
            pass
        self.position_changed.emit(emitted)

    def run(self) -> None:
        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            return
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self._reported_fps = fps if fps > 1.0 else _DEFAULT_FPS
        count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        self._frame_count = int(count) if count > 0 else 0
        self._pos = 0
        self.opened.emit(self._frame_count, self._reported_fps)
        self.capture_fps.emit(self.playback_fps())

        stat = ThreadStatisticsData()
        try:
            while not self._stop:
                # Block while paused. A seek sets the gate too, which is how a
                # scrub gets a single frame decoded without leaving pause.
                self._resume.wait()
                if self._stop:
                    break

                seek_to, token = self._take_seek()
                did_seek = seek_to is not None
                if did_seek:
                    self._pos = seek_to
                    cap.set(cv2.CAP_PROP_POS_FRAMES, float(self._pos))
                    self._reset_pacing()
                    self._at_end = False
                    # The gate was only opened to service this scrub; if the
                    # transport is not actually playing, re-park afterwards.
                    if not self._playing_intent:
                        self._resume.clear()
                elif not self._resume.is_set():
                    continue

                playing = self._playing_intent

                if playing and self._pos >= self._effective_out():
                    if self._loop:
                        self._pos = self._in_frame
                        cap.set(cv2.CAP_PROP_POS_FRAMES, float(self._pos))
                        self._reset_pacing()
                    else:
                        self._park(max(0, self._effective_out() - 1))
                        continue

                ok, frame = cap.read()
                if not ok:
                    if playing and self._loop:
                        self._pos = self._in_frame
                        cap.set(cv2.CAP_PROP_POS_FRAMES, float(self._pos))
                        self._reset_pacing()
                        continue
                    # Natural EOF without loop: park at the end but stay alive,
                    # so the clip remains scrubbable.
                    self._park(max(0, self._frame_count - 1) if self._frame_count else self._pos)
                    continue

                emitted = self._pos
                self._pos += 1

                # A scrub frame already overtaken by a newer request is stale.
                if did_seek and self._seek_superseded(token):
                    continue

                # Scrub previews are emitted immediately; only real playback is paced.
                self._publish(frame, emitted, paced=playing)

                self.capture_fps.emit(self.playback_fps())
                stat.average_fps = int(round(self.playback_fps()))
                self.stats.emit(stat)
        finally:
            cap.release()

    def _park(self, frame: int) -> None:
        """Stop at ``frame`` and clear the play intent, without exiting."""
        self._at_end = True
        self._pos = frame + 1
        self._resume.clear()
        self._playing_intent = False
        self.position_changed.emit(frame)
        self.playing_changed.emit(False)
        self._reset_pacing()
