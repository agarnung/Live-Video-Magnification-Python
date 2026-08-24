"""Capture and processing threads (CaptureWorker / ProcessingWorker)."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, replace

import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from config import (
    CAPTURE_FPS_STAT_QUEUE_LENGTH,
    PROCESSING_FPS_STAT_QUEUE_LENGTH,
)
from camera_enumerator import preferred_capture_apis
from instrumentation import Instrumentation
from mat_to_qimage import mat_to_qimage
from magnificator import Magnificator
from structures import (
    DisplayFrame,
    ImageProcessingFlags,
    ImageProcessingSettings,
    ThreadStatisticsData,
    ViewMode,
)


@dataclass(frozen=True)
class ProcessorConfig:
    """
    An immutable snapshot of everything the processing loop needs per frame.

    RCU-style live config: the GUI *publishes* a whole new instance atomically
    (a single attribute rebind, which the GIL makes indivisible) and the
    processing loop *reads the reference once per frame*.  No lock is ever held
    across the two threads.

    This replaces a mutex that used to be held for the entire duration of a
    frame's processing -- pyramid construction included.  With that design,
    dragging a slider blocked the GUI thread for as long as a frame took, so the
    UI froze exactly when the user was interacting with it.  An immutable
    publish cannot block: worst case a slider move lands one frame late.
    """

    # ROI in source-frame coordinates; a zero width/height means "whole frame".
    roi: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Processing-resolution divisor (1, 2, 4 or 8); see PROCESSING_SCALES.
    downscale: int = 1
    grayscale_on: bool = False
    color_magnify_on: bool = False
    laplace_magnify_on: bool = False
    riesz_magnify_on: bool = False
    amplification: float = 0.0
    co_wavelength: float = 0.0
    co_low: float = 0.1
    co_high: float = 0.4
    chrom_attenuation: float = 0.0
    levels: int = 4
    framerate: float = 0.0
    # Bumped whenever a change invalidates the temporal state (mode, ROI,
    # levels, downscale).  The loop compares it against the generation it last
    # ran with, so it can reset the magnifier on its own thread instead of the
    # GUI thread mutating buffers the loop is reading.
    generation: int = 0


class CaptureWorker(QThread):
    """Reads frames from the camera and pushes them onto the queue."""

    frame_ready = pyqtSignal(np.ndarray)
    stats = pyqtSignal(object)
    capture_fps = pyqtSignal(float)

    def __init__(
        self,
        device_id: int,
        width: int,
        height: int,
        fps: int,
        drop_if_full: bool,
        frame_queue: queue.Queue,
        max_queue: int,
        instrumentation: Instrumentation | None = None,
    ) -> None:
        super().__init__()
        self._device_id = device_id
        self._width = width
        self._height = height
        self._fps = fps
        self._drop = drop_if_full
        self._queue = frame_queue
        self._max_queue = max_queue
        self._instr = instrumentation
        # Frames evicted by the latest-wins policy. Should stay at 0 for files;
        # for cameras it is the honest measure of how far behind we are.
        self._drops = 0
        self._stop = False
        self._cap: cv2.VideoCapture | None = None
        self._fps_samples: deque[float] = deque(maxlen=CAPTURE_FPS_STAT_QUEUE_LENGTH)

    def run(self) -> None:
        # Try the platform-native backend first and fall back to CAP_ANY: the
        # indices reported by the enumerator are V4L2 ordinals, so opening with
        # the same backend guarantees index and device stay in agreement.
        for api in preferred_capture_apis():
            cap = cv2.VideoCapture(self._device_id, api)
            if cap.isOpened():
                self._cap = cap
                break
            cap.release()
        if self._cap is None:
            return

        if self._width > 0:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        if self._height > 0:
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        if self._fps > 0:
            self._cap.set(cv2.CAP_PROP_FPS, self._fps)

        n = 0
        while not self._stop and self._cap.isOpened():
            t_loop = time.perf_counter()
            ok, frame = self._cap.read()
            if not ok:
                if self._instr is not None:
                    self._instr.on_source_read_error()
                break
            n += 1
            # Timestamp at capture so the latency histogram measures the real
            # capture->processed age, queueing included.
            stamped = (frame, time.perf_counter())
            if self._instr is not None:
                self._instr.on_captured()
            if self._drop:
                # Latest-wins: evict the oldest frame so the grab loop never
                # stalls. Dropping is a deliberate policy here, and is counted.
                if self._queue.full():
                    try:
                        self._queue.get_nowait()
                        self._drops += 1
                    except queue.Empty:
                        pass
                self._queue.put(stamped)
            else:
                # Lossless: block until the consumer catches up. The previous
                # code used put(timeout=0.5) and swallowed queue.Full, which
                # silently dropped frames even with dropping disabled -- that
                # breaks the continuity the temporal filters depend on.
                self._queue.put(stamped)

            if self._instr is not None:
                self._instr.set_source_drops(self._drops)
                self._instr.set_queue_depth(self._queue.qsize())

            dt = time.perf_counter() - t_loop
            if dt > 0:
                # Float division: the old `1000 // dt_ms` quantised the rate so
                # coarsely that 60 fps could only ever read as 62 or 76.
                self._fps_samples.append(1.0 / dt)
            if n % 30 == 0 and self._fps_samples:
                avg = sum(self._fps_samples) / len(self._fps_samples)
                real_fps = self._cap.get(cv2.CAP_PROP_FPS)
                self.capture_fps.emit(float(real_fps or avg))

            st = ThreadStatisticsData()
            st.average_fps = int(
                sum(self._fps_samples) // max(1, len(self._fps_samples))
            )
            self.stats.emit(st)

        if self._cap:
            self._cap.release()


class ProcessingWorker(QThread):
    """Magnifies queued frames and publishes {processed, original} pairs."""

    # One signal carrying ONE DisplayFrame, never two signals: see DisplayFrame.
    new_frame = pyqtSignal(object)
    stats = pyqtSignal(object)
    max_levels = pyqtSignal(int)

    def __init__(
        self,
        frame_queue: queue.Queue,
        instrumentation: Instrumentation | None = None,
    ) -> None:
        super().__init__()
        self._queue = frame_queue
        self._instr = instrumentation
        self._stop = False

        # --- published config (written by the GUI thread, read by run()) -----
        # A single attribute rebind is atomic under the GIL, so no lock is
        # needed on either side; the dataclass being frozen guarantees the loop
        # cannot observe a half-updated config.
        self._config = ProcessorConfig()
        self._generation = 0
        # Held only for the read-modify-write of the config reference, never
        # across any processing. Needed because both the GUI thread and the
        # processing thread's fps feedback derive a new config from the current
        # one, and an unguarded replace() could lose the other's update.
        self._publish_lock = threading.Lock()

        # --- loop-private state ---------------------------------------------
        # The Magnificator holds references to these two mutable objects, so
        # they must stay the same instances for its lifetime; the loop copies
        # the published config's fields into them once per frame, on its own
        # thread.  That keeps the algorithm modules untouched.
        self._flags = ImageProcessingFlags()
        self._settings = ImageProcessingSettings()
        self._roi = (0, 0, 0, 0)
        # Views that never show the original pane do not need the extra QImage
        # conversion; ORIGINAL additionally bypasses magnification altogether.
        self._view_mode = ViewMode.PROCESSED
        self._need_max_levels_for_full = True
        self._processing_buffer: list[np.ndarray] = []
        self._buffer_len = 2
        self._magnificator = Magnificator(
            self._processing_buffer, self._flags, self._settings
        )
        self._applied_generation = -1
        self._need_max_levels_for_full = True
        self._proc_times: deque[int] = deque(maxlen=PROCESSING_FPS_STAT_QUEUE_LENGTH)
        self._sample_n = 0

    # ---- GUI-thread setters: publish, never lock -------------------------

    def _publish(self, **changes) -> None:
        """Atomically swap in a new immutable config derived from the current one."""
        with self._publish_lock:
            self._config = replace(self._config, **changes)

    def _publish_invalidating(self, **changes) -> None:
        """Publish a change that makes the accumulated temporal state stale."""
        with self._publish_lock:
            self._generation += 1
            self._config = replace(
                self._config, generation=self._generation, **changes
            )

    def set_roi(self, x: int, y: int, w: int, h: int) -> None:
        """
        Publish a new ROI.

        Levels are recomputed here rather than in the loop because
        calculate_max_levels is pure arithmetic on the ROI size -- no filter
        state involved -- so it is safe to call from the GUI thread.
        """
        self._publish_invalidating(roi=(x, y, w, h))
        if w > 0 and h > 0:
            self._need_max_levels_for_full = False
            self.max_levels.emit(self._magnificator.calculate_max_levels(w, h))
        else:
            self._need_max_levels_for_full = True

    def set_downscale(self, divisor: int) -> None:
        """
        Set the processing-resolution divisor (1, 2, 4 or 8).

        Changing it alters the frame geometry, so the temporal state is
        invalidated: the magnification filters hold per-pixel history that is
        meaningless at a different resolution.
        """
        divisor = int(divisor) if divisor in (1, 2, 4, 8) else 1
        if divisor == self._config.downscale:
            return
        self._need_max_levels_for_full = True
        self._publish_invalidating(downscale=divisor)

    def set_view_mode(self, mode: ViewMode) -> None:
        """
        Select which panes the display needs.

        In ORIGINAL mode the magnification output is never shown, so the whole
        pyramid is skipped -- that is the point of the bypass, it buys back the
        CPU. The temporal state is dropped on the way in and out because the
        filters would otherwise resume with a history full of frames they never
        saw, producing a visible transient.
        """
        changed = mode is not self._view_mode
        bypass_toggled = changed and (
            mode is ViewMode.ORIGINAL or self._view_mode is ViewMode.ORIGINAL
        )
        # No mutex needed: this runs on the GUI thread, the loop runs on its
        # own, and a plain attribute assignment is atomic under the GIL -- the
        # same reasoning that lets _config be published without a lock.
        self._view_mode = mode
        if bypass_toggled:
            self._processing_buffer.clear()
            self._magnificator.clear_buffer()

    def update_flags(self, f: ImageProcessingFlags) -> None:
        """A mode switch always invalidates the filter history."""
        self._publish_invalidating(
            grayscale_on=f.grayscale_on,
            color_magnify_on=f.color_magnify_on,
            laplace_magnify_on=f.laplace_magnify_on,
            riesz_magnify_on=f.riesz_magnify_on,
        )

    def update_settings(self, s: ImageProcessingSettings) -> None:
        """
        Publish new magnification parameters.

        Only a change of pyramid depth invalidates the state; amplification and
        the cutoffs are read fresh every frame, so they can be dragged live.
        """
        changes = dict(
            amplification=s.amplification,
            co_wavelength=s.co_wavelength,
            co_low=s.co_low,
            co_high=s.co_high,
            chrom_attenuation=s.chrom_attenuation,
            levels=s.levels,
            framerate=s.framerate,
        )
        if s.levels != self._config.levels:
            self._publish_invalidating(**changes)
        else:
            self._publish(**changes)

    def update_framerate(self, fps: float) -> None:
        self._publish(framerate=fps)

    def stop(self) -> None:
        self._stop = True

    # ---- processing thread ----------------------------------------------

    def _sync_config(self, cfg: ProcessorConfig) -> None:
        """
        Copy the published config into the mutable objects the Magnificator
        aliases, and reset the temporal state if the generation moved on.
        """
        self._flags.grayscale_on = cfg.grayscale_on
        self._flags.color_magnify_on = cfg.color_magnify_on
        self._flags.laplace_magnify_on = cfg.laplace_magnify_on
        self._flags.riesz_magnify_on = cfg.riesz_magnify_on
        self._settings.amplification = cfg.amplification
        self._settings.co_wavelength = cfg.co_wavelength
        self._settings.co_low = cfg.co_low
        self._settings.co_high = cfg.co_high
        self._settings.chrom_attenuation = cfg.chrom_attenuation
        self._settings.levels = cfg.levels
        self._settings.framerate = cfg.framerate
        if cfg.generation != self._applied_generation:
            self._applied_generation = cfg.generation
            self._reset_temporal_state()

    def _reset_temporal_state(self) -> None:
        """Drop every accumulated buffer; the next frames rebuild the history."""
        self._processing_buffer.clear()
        self._magnificator.clear_buffer()

    def run(self) -> None:
        while not self._stop:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if isinstance(item, tuple):
                frame, capture_ts = item
            else:
                # Tolerate un-stamped frames from older producers.
                frame, capture_ts = item, time.perf_counter()

            t0 = time.perf_counter()
            # Read the live config exactly once per frame: a mid-frame change
            # would otherwise mix parameters from two different settings within
            # a single pyramid.
            cfg = self._config
            self._sync_config(cfg)

            try:
                out_disp, orig_disp = self._process_frame(frame, cfg)
            except Exception:
                # A stage threw. Don't let it kill the QThread silently, which
                # is what happened before: the tab simply stopped updating with
                # no indication why. Count the error, reset the stateful stages
                # (a mid-frame throw can leave temporal state half-updated) and
                # degrade to publishing the input frame.
                if self._instr is not None:
                    self._instr.on_processing_error()
                self._reset_temporal_state()
                out_disp = self._to_display(frame)
                orig_disp = out_disp

            dt_ms = int((time.perf_counter() - t0) * 1000)
            self._update_fps_stats(dt_ms)
            if self._instr is not None:
                self._instr.on_processed()
                self._instr.record_latency((time.perf_counter() - capture_ts) * 1000.0)
                self._instr.set_queue_depth(self._queue.qsize())
            # Both panes leave the worker in the same object, so the display
            # cannot pair a processed frame with a different original.
            need_original = self._view_mode is not ViewMode.PROCESSED
            self.new_frame.emit(
                DisplayFrame(
                    processed=mat_to_qimage(out_disp),
                    original=mat_to_qimage(orig_disp) if (need_original and orig_disp is not None) else None,
                )
            )

            st = ThreadStatisticsData()
            st.n_frames_processed = 1
            if self._proc_times:
                st.average_fps = sum(self._proc_times) // len(self._proc_times)
            self.stats.emit(st)

    @staticmethod
    def _to_display(img: np.ndarray) -> np.ndarray:
        """Widen a single-channel result so mat_to_qimage always sees BGR."""
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img

    def _process_frame(
        self, frame: np.ndarray, cfg: ProcessorConfig
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Crop, downscale and magnify one frame. May raise; the caller degrades.

        Returns ``(processed, original)``. The "original" tap is taken
        post-ROI/downscale/grayscale but PRE-magnification -- the same
        first-stage tap the C++ chain exposes -- so the comparison views show
        what the algorithm actually saw, not the raw camera frame. It is taken
        before the buffer is handed to the magnificator, which may modify its
        entries in place.
        """
        x, y, rw, rh = cfg.roi
        fh, fw = frame.shape[:2]
        emit_lv_full: int | None = None
        if rw <= 0 or rh <= 0:
            x, y, rw, rh = 0, 0, fw, fh
            if self._need_max_levels_for_full:
                self._need_max_levels_for_full = False
                emit_lv_full = self._magnificator.calculate_max_levels(rw, rh)

        # Clamp the ROI to the frame: a stale ROI kept after the source changed
        # resolution would otherwise index out of bounds.
        x = max(0, min(x, fw - 1))
        y = max(0, min(y, fh - 1))
        rw = max(1, min(rw, fw - x))
        rh = max(1, min(rh, fh - y))
        cur = frame[y : y + rh, x : x + rw].copy()

        # Processing resolution: dividing each side by `downscale` cuts the
        # pyramid cost quadratically, which is the single most effective
        # performance control (1/4 is ~16x less work). INTER_AREA is the right
        # filter for shrinking.
        if cfg.downscale > 1:
            dh = max(1, cur.shape[0] // cfg.downscale)
            dw = max(1, cur.shape[1] // cfg.downscale)
            cur = cv2.resize(cur, (dw, dh), interpolation=cv2.INTER_AREA)

        if cfg.grayscale_on and cur.ndim == 3:
            cur = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY)

        original = self._to_display(cur.copy())

        out = cur
        # ORIGINAL view bypasses magnification entirely: nothing downstream
        # reads the pyramid result, so there is no reason to pay for it.
        if self._view_mode is not ViewMode.ORIGINAL:
            self._processing_buffer.append(cur)
            if len(self._processing_buffer) == self._buffer_len:
                if cfg.color_magnify_on:
                    self._magnificator.color_magnify()
                    if self._magnificator.has_frame():
                        out = self._magnificator.get_frame_last()
                elif cfg.laplace_magnify_on:
                    self._magnificator.laplace_magnify()
                    if self._magnificator.has_frame():
                        out = self._magnificator.get_frame_last()
                elif cfg.riesz_magnify_on:
                    self._magnificator.riesz_magnify()
                    if self._magnificator.has_frame():
                        out = self._magnificator.get_frame_last()
                else:
                    self._processing_buffer.pop(0)

        if emit_lv_full is not None:
            self.max_levels.emit(emit_lv_full)
        return self._to_display(out), original

    def _update_fps_stats(self, elapsed_ms: int) -> None:
        if elapsed_ms > 0:
            self._proc_times.append(1000 // elapsed_ms)
            self._sample_n += 1
        if (
            len(self._proc_times) == PROCESSING_FPS_STAT_QUEUE_LENGTH
            and self._sample_n >= PROCESSING_FPS_STAT_QUEUE_LENGTH
        ):
            # Feed the measured rate back as the framerate the Hz maths uses,
            # published like any other config change so the loop picks it up on
            # its next frame.
            measured = sum(self._proc_times) // PROCESSING_FPS_STAT_QUEUE_LENGTH
            self._publish(framerate=float(measured))
            self._proc_times.clear()
            self._sample_n = 0

    def downscale(self) -> int:
        """Current processing-resolution divisor, so an export can match the preview."""
        return self._config.downscale
