"""Camera or file tab: capture + processing + options."""

from __future__ import annotations

import queue
import time

import cv2
from PyQt6.QtCore import QThread, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from exporter import ExportPhase, Exporter
from structures import DisplayFrame, ThreadStatisticsData, ViewMode
from ui.export_dialogs import ExportProgressDialog, ExportSeed, ExportSettingsDialog
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

        # Export state: the source path (files only) is what the exporter
        # re-reads, so a camera tab has nothing to export from.
        self._source_path: str | None = None if use_camera else str(kwargs["path"])
        self._exporter = Exporter()
        self._export_dialog: ExportProgressDialog | None = None
        self._export_timer = QTimer(self)
        self._export_timer.setInterval(150)
        self._export_timer.timeout.connect(self._poll_export)
        self._roi: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._capture_fps = 30.0

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QVBoxLayout()

        # View selector: the four comparison modes of the C++ DisplayWidget.
        bar = QHBoxLayout()
        bar.addWidget(QLabel("View:"))
        self._view = QComboBox()
        self._view.addItem("Processed", ViewMode.PROCESSED)
        self._view.addItem("Original", ViewMode.ORIGINAL)
        self._view.addItem("Side by side", ViewMode.SIDE_BY_SIDE)
        self._view.addItem("Stacked", ViewMode.STACKED)
        self._view.currentIndexChanged.connect(self._on_view_changed)
        bar.addWidget(self._view)
        bar.addStretch(1)
        self._export_btn = QPushButton("Export video…")
        self._export_btn.setEnabled(self._source_path is not None)
        if self._source_path is None:
            self._export_btn.setToolTip("Export is available for file sources only.")
        self._export_btn.clicked.connect(self._on_export)
        bar.addWidget(self._export_btn)

        self._label = FrameLabel()
        self._label.setText("Waiting for video…")
        self._fps_lbl = QLabel("FPS: —")
        left_w = QWidget()
        left_w.setLayout(left)
        left.addLayout(bar)
        left.addWidget(self._label, 1)
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
        self._opts.downscale_changed.connect(self._proc.set_downscale)
        self._label.roi_changed.connect(self._on_roi)
        self._reader.capture_fps.connect(self._proc.update_framerate)
        # Keep the Nyquist clamp of the cutoff sliders in step with the rate the
        # source actually delivers. Both reader kinds expose the same signal.
        self._reader.capture_fps.connect(self._opts.set_capture_fps)
        self._reader.capture_fps.connect(self._on_capture_fps)

        self._proc.start()
        self._reader.start()

    def _on_roi(self, r: QRect) -> None:
        self._roi = (r.x(), r.y(), r.width(), r.height())
        self._proc.set_roi(r.x(), r.y(), r.width(), r.height())

    def _on_capture_fps(self, fps: float) -> None:
        if fps and fps > 0:
            self._capture_fps = float(fps)

    def _on_view_changed(self, index: int) -> None:
        """Tell both the display and the worker: only the worker can skip work."""
        mode: ViewMode = self._view.itemData(index)
        self._label.set_view_mode(mode)
        self._proc.set_view_mode(mode)

    def _on_frame(self, frame: DisplayFrame) -> None:
        self._label.set_display_frame(frame)

    def _on_stats(self, st) -> None:
        self._fps_lbl.setText(f"Processing ~{getattr(st, 'average_fps', 0)} FPS")

    # ------------------------------------------------------------------ export

    def _frame_count(self) -> int:
        """Probe the file's length for the trim spinboxes; 0 when unknown."""
        if self._source_path is None:
            return 0
        cap = cv2.VideoCapture(self._source_path)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) if cap.isOpened() else 0
        cap.release()
        return max(0, n)

    def _on_export(self) -> None:
        if self._source_path is None:
            return
        if self._exporter.is_running():
            QMessageBox.information(self, "Export", "An export is already running.")
            return
        seed = ExportSeed(
            source_path=self._source_path,
            settings=self._opts.current_settings(),
            flags=self._opts.current_flags(),
            roi=self._roi,
            downscale=self._proc.downscale(),
            capture_fps=self._capture_fps,
            frame_count=self._frame_count(),
            max_levels=self._opts.max_levels(),
        )
        dlg = ExportSettingsDialog(seed, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        request = dlg.request()
        if request is None or not request.output_path:
            return

        self._export_dialog = ExportProgressDialog(self)
        self._export_dialog.aborted.connect(self._exporter.abort)
        self._exporter.start(request)
        self._export_timer.start()
        self._export_dialog.exec()

    def _poll_export(self) -> None:
        """
        Pull the worker's progress snapshot.

        Polling rather than signalling keeps the encoder thread free of Qt: it
        publishes a plain dataclass under a lock and never blocks on the GUI.
        """
        if self._export_dialog is None:
            return
        p = self._exporter.progress()
        if p.phase in (ExportPhase.PROCESSING, ExportPhase.FINALIZING):
            note = "Finalizing…" if p.phase is ExportPhase.FINALIZING else "Encoding"
            self._export_dialog.set_progress(p.frames_done, p.frames_total, note)
            return

        self._export_timer.stop()
        dlg, self._export_dialog = self._export_dialog, None
        if p.phase is ExportPhase.DONE:
            w, h = p.frame_size
            dlg.mark_finished(
                f"Done: {p.frames_done} frames, {w}x{h}, codec {p.codec_used}\n"
                f"{p.output_path}"
            )
        elif p.phase is ExportPhase.ABORTED:
            dlg.mark_finished("Cancelled; the partial file was removed.")
        else:
            dlg.mark_finished(p.error or "Export failed.")

    def shutdown(self) -> None:
        self._export_timer.stop()
        # Abort before joining: the encoder loop only checks the flag between
        # frames, so a running export would otherwise hold the app open.
        self._exporter.abort()
        self._exporter.join(10.0)
        self._proc.stop()
        self._reader._stop = True
        self._reader.wait(5000)
        self._proc.wait(5000)
