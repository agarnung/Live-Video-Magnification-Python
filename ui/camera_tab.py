"""Camera or file tab: capture + processing + playback transport + options."""

from __future__ import annotations

import queue

from PyQt6.QtCore import QThread, QRect, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from playback import VideoFileWorker
from ui.frame_label import FrameLabel
from ui.magnify_options import MagnifyOptions
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
        self._queue: queue.Queue = queue.Queue(maxsize=qsize)
        self._proc = ProcessingWorker(self._queue)
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
        self._label.setText("Waiting for video\u2026")
        self._fps_lbl = QLabel("FPS: \u2014")
        left_w = QWidget()
        left_w.setLayout(left)
        left.addWidget(self._label)

        self._timeline: TimelineView | None = None
        self._transport: QWidget | None = None
        if self._is_file:
            self._timeline = TimelineView()
            self._transport = self._build_transport()
            left.addWidget(self._timeline)
            left.addWidget(self._transport)
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
        # Keep the Nyquist clamp of the cutoff sliders in step with the
        # rate the source actually delivers.
        self._reader.capture_fps.connect(self._opts.set_capture_fps)

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
        self._proc.set_roi(r.x(), r.y(), r.width(), r.height())

    def _on_frame(self, img: QImage) -> None:
        self._label.set_image(img)

    def _on_stats(self, st) -> None:
        self._fps_lbl.setText(f"Processing ~{getattr(st, 'average_fps', 0)} FPS")

    def shutdown(self) -> None:
        self._proc.stop()
        if isinstance(self._reader, VideoFileWorker):
            # stop() only rewinds; shutdown() is what ends the decode thread.
            self._reader.shutdown()
        else:
            self._reader._stop = True
        self._reader.wait(5000)
        self._proc.wait(5000)
