"""Camera or file tab: capture + processing + options."""

from __future__ import annotations

import queue
import time

import cv2
from PyQt6.QtCore import QThread, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QLabel, QSplitter, QVBoxLayout, QWidget

from structures import ThreadStatisticsData
from ui.frame_label import FrameLabel
from ui.magnify_options import MagnifyOptions
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
    ) -> None:
        super().__init__()
        self._path = path
        self._queue = frame_queue
        self._drop = drop_if_full
        self._stop = False

    def run(self) -> None:
        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            return
        file_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        delay = 1.0 / max(1.0, file_fps)
        while not self._stop:
            t0 = time.perf_counter()
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            if self._drop and self._queue.full():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
            try:
                self._queue.put(frame, timeout=0.5)
            except queue.Full:
                pass
            self.capture_fps.emit(file_fps)
            st = ThreadStatisticsData()
            st.average_fps = int(file_fps)
            self.stats.emit(st)
            elapsed = time.perf_counter() - t0
            if elapsed < delay:
                time.sleep(delay - elapsed)
        cap.release()


class CameraTab(QWidget):
    """Main widget for one source (camera or file)."""

    def __init__(self, use_camera: bool, **kwargs) -> None:
        super().__init__()
        qsize = int(kwargs.get("queue_size", 8))
        self._queue: queue.Queue = queue.Queue(maxsize=qsize)
        self._proc = ProcessingWorker(self._queue)
        if use_camera:
            self._reader: QThread = CaptureWorker(
                int(kwargs["device_id"]),
                int(kwargs.get("width", 0)),
                int(kwargs.get("height", 0)),
                int(kwargs.get("fps", 0)),
                bool(kwargs.get("drop", False)),
                self._queue,
                qsize,
            )
        else:
            self._reader = VideoFileWorker(
                str(kwargs["path"]),
                self._queue,
                bool(kwargs.get("drop", False)),
            )

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QVBoxLayout()
        self._label = FrameLabel()
        self._label.setText("Waiting for video…")
        self._fps_lbl = QLabel("FPS: —")
        left_w = QWidget()
        left_w.setLayout(left)
        left.addWidget(self._label)
        left.addWidget(self._fps_lbl)

        self._opts = MagnifyOptions()
        split.addWidget(left_w)
        split.addWidget(self._opts)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)

        outer = QVBoxLayout(self)
        outer.addWidget(split)

        self._proc.new_frame.connect(self._on_frame)
        self._proc.stats.connect(self._on_stats)
        self._proc.max_levels.connect(self._opts.set_max_levels)
        self._opts.flags_changed.connect(self._proc.update_flags)
        self._opts.settings_changed.connect(self._proc.update_settings)
        self._label.roi_changed.connect(self._on_roi)
        if isinstance(self._reader, CaptureWorker):
            self._reader.capture_fps.connect(self._proc.update_framerate)
        else:
            self._reader.capture_fps.connect(self._proc.update_framerate)

        self._proc.start()
        self._reader.start()

    def _on_roi(self, r: QRect) -> None:
        self._proc.set_roi(r.x(), r.y(), r.width(), r.height())

    def _on_frame(self, img: QImage) -> None:
        self._label.set_image(img)

    def _on_stats(self, st) -> None:
        self._fps_lbl.setText(f"Processing ~{getattr(st, 'average_fps', 0)} FPS")

    def shutdown(self) -> None:
        self._proc.stop()
        if isinstance(self._reader, CaptureWorker):
            self._reader._stop = True
        else:
            self._reader._stop = True
        self._reader.wait(5000)
        self._proc.wait(5000)
