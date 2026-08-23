"""Camera / file connection dialog (CameraConnectDialog)."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QFileDialog,
)


class ConnectDialog(QDialog):
    """Source selection: camera (V4L index) or path to a video file."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect / Open")
        self._use_camera = True
        self._path = ""

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self._btn_cam = QPushButton("Camera")
        self._btn_vid = QPushButton("Video file")
        self._btn_cam.setCheckable(True)
        self._btn_vid.setCheckable(True)
        self._btn_cam.setChecked(True)
        self._btn_cam.clicked.connect(self._pick_cam)
        self._btn_vid.clicked.connect(self._pick_vid)
        row.addWidget(self._btn_cam)
        row.addWidget(self._btn_vid)
        lay.addLayout(row)

        form = QFormLayout()
        self._device = QSpinBox()
        self._device.setRange(0, 99)
        self._width = QSpinBox()
        self._width.setRange(0, 7680)
        self._height = QSpinBox()
        self._height.setRange(0, 4320)
        self._fps = QSpinBox()
        self._fps.setRange(0, 240)
        self._buf = QSpinBox()
        self._buf.setRange(1, 64)
        self._buf.setValue(8)
        self._drop = QCheckBox("Drop frames when the queue is full")
        self._path_edit = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)

        form.addRow("Device index", self._device)
        form.addRow("Width (0 = auto)", self._width)
        form.addRow("Height (0 = auto)", self._height)
        form.addRow("FPS (0 = auto)", self._fps)
        form.addRow("Queue size", self._buf)
        form.addRow(self._drop)
        form.addRow("File", self._path_edit)
        form.addRow(browse)
        lay.addLayout(form)

        self._label_hint = QLabel()
        self._label_hint.setWordWrap(True)
        lay.addWidget(self._label_hint)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

        self._pick_cam()

    def _pick_cam(self) -> None:
        self._use_camera = True
        self._btn_cam.setChecked(True)
        self._btn_vid.setChecked(False)
        self._label_hint.setText(
            "Camera index (0 is usually the built-in webcam). Note that not "
            "every /dev/videoN is a usable camera: on many laptops the odd "
            "indices are metadata nodes."
        )

    def _pick_vid(self) -> None:
        self._use_camera = False
        self._btn_vid.setChecked(True)
        self._btn_cam.setChecked(False)
        self._label_hint.setText("Choose a .mp4, .avi, .mkv, .mov or .webm file.")

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open video",
            "",
            "Video (*.mp4 *.avi *.mkv *.mov *.webm);;All files (*.*)",
        )
        if path:
            self._path_edit.setText(path)

    def is_camera(self) -> bool:
        return self._use_camera

    def device_id(self) -> int:
        return self._device.value()

    def width(self) -> int:
        return self._width.value()

    def height(self) -> int:
        return self._height.value()

    def fps(self) -> int:
        return self._fps.value()

    def queue_size(self) -> int:
        return self._buf.value()

    def drop_frames(self) -> bool:
        return self._drop.isChecked()

    def video_path(self) -> str:
        return self._path_edit.text().strip()
