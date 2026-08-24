"""Camera or file tab: capture + processing + playback transport + options."""

from __future__ import annotations

import queue

import cv2
from PyQt6.QtCore import QThread, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from exporter import ExportPhase, Exporter
from instrumentation import Instrumentation
from playback import VideoFileWorker
from structures import DisplayFrame, ThreadStatisticsData, ViewMode
from ui.export_dialogs import ExportProgressDialog, ExportSeed, ExportSettingsDialog
from ui.frame_label import FrameLabel
from ui.magnify_options import MagnifyOptions
from ui.status_strip import StatusStrip
from ui.timeline import TimelineView
from workers import CaptureWorker, ProcessingWorker


class CameraTab(QWidget):
    """Main widget for one source (camera or file).

    A file gets a transport bar and a timeline; a camera does not, because a
    live sensor is not seekable and offering play/seek controls for it would
    only promise something the source cannot deliver.
    """

    # Playback-speed multipliers offered relative to the file's native rate.
    _MIN_SPEED = 0.1
    _MAX_SPEED = 8.0

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
        self._is_file = not use_camera
        # Playback was running when the current scrub began, so it must resume
        # on release. Captured before pausing, since pausing destroys the answer.
        self._scrub_resume = False

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
        self._label.setText("Waiting for video\u2026")
        left_w = QWidget()
        left_w.setLayout(left)
        left.addLayout(bar)
        left.addWidget(self._label, 1)

        self._timeline: TimelineView | None = None
        self._transport: QWidget | None = None
        if self._is_file:
            self._timeline = TimelineView()
            self._transport = self._build_transport()
            left.addWidget(self._timeline)
            left.addWidget(self._transport)

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
        # source actually delivers. Both reader kinds expose the same signal.
        self._reader.capture_fps.connect(self._opts.set_capture_fps)
        self._reader.capture_fps.connect(self._on_capture_fps)

        if self._is_file:
            self._connect_transport()

        self._proc.start()
        self._reader.start()
        if self._is_file:
            # A file is opened parked; start rolling so the tab behaves like
            # before unless the user takes the transport over.
            self._reader.play()

    # ------------------------------------------------------------------
    # Transport UI (file sources only)
    # ------------------------------------------------------------------

    def _build_transport(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        self._play_btn = QPushButton("Pause")
        self._play_btn.setToolTip("Play / pause playback")
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setToolTip("Rewind to the in-point and pause")
        self._loop_chk = QCheckBox("Loop")
        self._loop_chk.setToolTip("Restart at the in-point when the out-point is reached")

        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(self._MIN_SPEED, self._MAX_SPEED)
        self._speed_spin.setSingleStep(0.25)
        self._speed_spin.setValue(1.0)
        self._speed_spin.setSuffix("x")
        self._speed_spin.setToolTip("Playback speed relative to the file's native rate")

        row.addWidget(self._play_btn)
        row.addWidget(self._stop_btn)
        row.addWidget(self._loop_chk)
        row.addStretch(1)
        row.addWidget(QLabel("Speed"))
        row.addWidget(self._speed_spin)
        return bar

    def _connect_transport(self) -> None:
        reader = self._reader
        timeline = self._timeline
        assert isinstance(reader, VideoFileWorker) and timeline is not None

        self._play_btn.clicked.connect(self._toggle_play)
        self._stop_btn.clicked.connect(reader.stop)
        self._loop_chk.toggled.connect(reader.set_loop)
        self._speed_spin.valueChanged.connect(self._on_speed)

        reader.opened.connect(self._on_opened)
        reader.position_changed.connect(timeline.set_playhead_frame)
        reader.playing_changed.connect(self._on_playing_changed)

        timeline.seek_requested.connect(reader.seek_frame)
        timeline.in_out_changed.connect(reader.set_in_out)
        # A scrub freezes playback while the handle is held and resumes on
        # release, so the decoder is not racing the drag for the same capture.
        timeline.scrub_started.connect(self._on_scrub_started)
        timeline.scrub_finished.connect(self._on_scrub_finished)

    def _toggle_play(self) -> None:
        reader = self._reader
        if reader.is_playing():
            reader.pause()
        else:
            reader.play()

    def _on_speed(self, factor: float) -> None:
        """Translate the speed multiplier into an absolute target cadence."""
        reader = self._reader
        reader.set_playback_fps(reader.reported_fps() * float(factor))
        if self._timeline is not None:
            # The clock label reads wall-clock time at the playback rate.
            self._timeline.set_fps(reader.playback_fps())

    def _on_opened(self, frame_count: int, fps: float) -> None:
        """Seed the timeline once the container has been probed."""
        timeline = self._timeline
        if timeline is None:
            return
        timeline.set_frame_count(frame_count)
        timeline.set_in_out(0, -1)  # whole clip by default
        timeline.set_fps(self._reader.playback_fps())
        # A container with no usable frame count cannot be seeked meaningfully.
        seekable = self._reader.seekable()
        timeline.setEnabled(seekable)
        timeline.setVisible(seekable)

    def _on_playing_changed(self, playing: bool) -> None:
        self._play_btn.setText("Pause" if playing else "Play")

    def _on_scrub_started(self) -> None:
        self._scrub_resume = self._reader.is_playing()
        self._reader.pause()

    def _on_scrub_finished(self) -> None:
        if self._scrub_resume:
            self._reader.play()
        self._scrub_resume = False

    # ------------------------------------------------------------------

    def _on_roi(self, r: QRect) -> None:
        self._roi = (r.x(), r.y(), r.width(), r.height())
        self._proc.set_roi(r.x(), r.y(), r.width(), r.height())

    def _on_view_changed(self, index: int) -> None:
        """Tell both the display and the worker: only the worker can skip work."""
        mode: ViewMode = self._view.itemData(index)
        self._label.set_view_mode(mode)
        self._proc.set_view_mode(mode)

    def _on_frame(self, frame: DisplayFrame) -> None:
        self._label.set_display_frame(frame)
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

    def _frame_count(self) -> int:
        """Probe the file's length for the trim spinboxes; 0 when unknown."""
        if self._source_path is None:
            return 0
        cap = cv2.VideoCapture(self._source_path)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) if cap.isOpened() else 0
        cap.release()
        return max(0, n)

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
        self._poll.stop()
        self._export_timer.stop()
        # Abort before joining: the encoder loop only checks the flag between
        # frames, so a running export would otherwise hold the app open.
        self._exporter.abort()
        self._exporter.join(10.0)
        self._proc.stop()
        if isinstance(self._reader, VideoFileWorker):
            # stop() only rewinds; shutdown() is what ends the decode thread.
            self._reader.shutdown()
        else:
            self._reader._stop = True
            self._reader.wait(5000)
        self._proc.wait(5000)
