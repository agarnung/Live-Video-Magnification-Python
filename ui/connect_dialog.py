"""Diálogo conectar cámara o archivo (CameraConnectDialog)."""

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
    """Selección de fuente: cámara (índice V4L) o ruta de vídeo."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Conectar / Abrir")
        self._use_camera = True
        self._path = ""

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self._btn_cam = QPushButton("Cámara")
        self._btn_vid = QPushButton("Archivo de vídeo")
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
        self._drop = QCheckBox("Descartar frames si la cola está llena")
        self._path_edit = QLineEdit()
        browse = QPushButton("Examinar…")
        browse.clicked.connect(self._browse)

        form.addRow("Dispositivo", self._device)
        form.addRow("Ancho (0=auto)", self._width)
        form.addRow("Alto (0=auto)", self._height)
        form.addRow("FPS (0=auto)", self._fps)
        form.addRow("Tamaño cola", self._buf)
        form.addRow(self._drop)
        form.addRow("Archivo", self._path_edit)
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
            "Índice de cámara (0 suele ser la webcam). En Linux se usa el backend por defecto de OpenCV."
        )

    def _pick_vid(self) -> None:
        self._use_camera = False
        self._btn_vid.setChecked(True)
        self._btn_cam.setChecked(False)
        self._label_hint.setText("Elija un archivo .mp4, .avi, .mkv, etc.")

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Vídeo",
            "",
            "Vídeo (*.mp4 *.avi *.mkv *.mov *.webm);;Todos (*.*)",
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
