"""Camera or file tab: capture + processing + options."""

from __future__ import annotations

import queue
import time

import cv2
from PyQt6.QtCore import QThread, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from instrumentation import Instrumentation
from structures import ThreadStatisticsData
from ui.frame_label import FrameLabel
from ui.magnify_options import MagnifyOptions
from ui.status_strip import StatusStrip
from workers import CaptureWorker, ProcessingWorker


class VideoFileWorker(QThread):
    """Reads a video file and pushes frames onto the queue."""

    stats = pyqtSignal(object)
    capture_fps = pyqtSignal(float)

    def __init__(
        self,
        path: str,
        frame_queue: queue.Queue,
        drop_if_full: bool,
        instrumentation: Instrumentation | None = None,
    ) -> None:
        super().__init__()
        self._path = path
        self._queue = frame_queue
        self._drop = drop_if_full
        self._instr = instrumentation
        self._drops = 0
        self._stop = False
        # Playback cadence, live-settable from the status strip's spinbox.
        self._playback_fps = 0.0

    def run(self) -> None:
        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            return
        file_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        if self._playback_fps <= 0.0:
            self._playback_fps = file_fps
        while not self._stop:
            t0 = time.perf_counter()
            # Read the pacing target once per frame, so the strip's spinbox
            # takes effect without any lock shared with the GUI thread.
            delay = 1.0 / max(1.0, self._playback_fps)
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            if self._instr is not None:
                self._instr.on_captured()
            if self._drop and self._queue.full():
                try:
                    self._queue.get_nowait()
                    self._drops += 1
                except queue.Empty:
                    pass
            # Blocking put: swallowing queue.Full would drop frames silently and
            # break the continuity the temporal filters depend on.
            self._queue.put((frame, time.perf_counter()))
            if self._instr is not None:
                self._instr.set_source_drops(self._drops)
                self._instr.set_queue_depth(self._queue.qsize())
            self.capture_fps.emit(file_fps)
            st = ThreadStatisticsData()
            st.average_fps = int(file_fps)
            self.stats.emit(st)
            elapsed = time.perf_counter() - t0
            if elapsed < delay:
                time.sleep(delay - elapsed)
        cap.release()

    def set_playback_fps(self, fps: float) -> None:
        """Re-time playback. A plain float store is atomic under the GIL."""
        if fps > 0.0:
            self._playback_fps = float(fps)


class CameraTab(QWidget):
    """Main widget for one source (camera or file)."""

    def __init__(self, use_camera: bool, **kwargs) -> None:
        super().__init__()
        qsize = int(kwargs.get("queue_size", 8))
        self._is_camera = use_camera
        self._target_fps = 0.0
        # The file reader re-emits its nominal rate every frame; the spinbox is
        # primed once so a user override is not overwritten on the next emit.
        self._playback_primed = False
        self._queue: queue.Queue = queue.Queue(maxsize=qsize)
        self._instr = Instrumentation()
        self._proc = ProcessingWorker(self._queue, self._instr)
        if use_camera:
            self._reader: QThread = CaptureWorker(
                int(kwargs["device_id"]),
                int(kwargs.get("width", 0)),
                int(kwargs.get("height", 0)),
                int(kwargs.get("fps", 0)),
                bool(kwargs.get("drop", False)),
                self._queue,
                qsize,
                self._instr,
            )
        else:
            self._reader = VideoFileWorker(
                str(kwargs["path"]),
                self._queue,
                bool(kwargs.get("drop", False)),
                self._instr,
            )

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QVBoxLayout()
        self._label = FrameLabel()
        self._label.setText("Waiting for video…")
        left_w = QWidget()
        left_w.setLayout(left)
        left.addWidget(self._label)

        self._opts = MagnifyOptions()
        split.addWidget(left_w)
        split.addWidget(self._opts)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)

        self._strip = StatusStrip()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(split)
        outer.addWidget(self._strip)
        if not use_camera:
            self._strip.playback_fps_changed.connect(self._on_playback_fps)

        # The strip is refreshed by polling, never by a per-frame signal: at
        # 60 fps a signal per frame would queue 60 GUI repaints a second just
        # to redraw text that only changes perceptibly a few times a second.
        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self._refresh_stats)
        self._poll.start()

        self._proc.new_frame.connect(self._on_frame)
        self._proc.stats.connect(self._on_stats)
        self._proc.max_levels.connect(self._opts.set_max_levels)
        self._opts.flags_changed.connect(self._proc.update_flags)
        self._opts.settings_changed.connect(self._proc.update_settings)
        self._opts.downscale_changed.connect(self._proc.set_downscale)
        self._label.roi_changed.connect(self._on_roi)
        self._reader.capture_fps.connect(self._proc.update_framerate)
        # Keep the Nyquist clamp of the cutoff sliders in step with the rate the
        # source actually delivers.
        self._reader.capture_fps.connect(self._opts.set_capture_fps)
        self._reader.capture_fps.connect(self._on_capture_fps)

        self._proc.start()
        self._reader.start()

    def _on_roi(self, r: QRect) -> None:
        self._proc.set_roi(r.x(), r.y(), r.width(), r.height())

    def _on_frame(self, img: QImage) -> None:
        self._label.set_image(img)
        self._instr.on_displayed()

    def _on_stats(self, st) -> None:
        """Kept for compatibility; the visible readout comes from the poll."""

    def _on_capture_fps(self, fps: float) -> None:
        """Remember the source's nominal rate, the health denominator."""
        if fps > 0.0:
            self._target_fps = float(fps)
            if not self._is_camera and not self._playback_primed:
                self._playback_primed = True
                self._strip.set_playback_fps(fps)

    def _on_playback_fps(self, fps: float) -> None:
        if isinstance(self._reader, VideoFileWorker):
            self._reader.set_playback_fps(fps)

    def _refresh_stats(self) -> None:
        self._strip.set_stats(
            self._instr.snapshot(), self._target_fps, True, self._is_camera
        )

    def shutdown(self) -> None:
        self._poll.stop()
        self._proc.stop()
        if isinstance(self._reader, CaptureWorker):
            self._reader._stop = True
        else:
            self._reader._stop = True
        self._reader.wait(5000)
        self._proc.wait(5000)
