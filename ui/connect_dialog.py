"""Camera / file connection dialog (CameraConnectDialog)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QFileDialog,
)

from camera_enumerator import enumerate_cameras


class ConnectDialog(QDialog):
    """Source selection: a camera picked from a list, or a path to a video file.

    Cameras are listed by their driver-reported name instead of a bare index:
    the old spin box forced the user to guess which ``/dev/videoN`` was real,
    and half of them are metadata nodes that open but never deliver a frame.
    The manual index field stays as an escape hatch for devices the enumerator
    cannot see (a camera held open by another process, an exotic backend).
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect / Open")
        self._use_camera = True
        self._path = ""
        self._devices: list[tuple[int, str]] = []

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

        lay.addWidget(QLabel("Select a camera:"))
        self._camera_list = QListWidget()
        self._camera_list.itemDoubleClicked.connect(self._on_double_click)
        self._camera_list.itemSelectionChanged.connect(self._on_camera_selected)
        lay.addWidget(self._camera_list)

        refresh_row = QHBoxLayout()
        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.clicked.connect(self.refresh_cameras)
        refresh_row.addStretch(1)
        refresh_row.addWidget(self._btn_refresh)
        lay.addLayout(refresh_row)

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

        form.addRow("Device index (manual)", self._device)
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

        self.refresh_cameras()
        self._pick_cam()

    # --- camera list ------------------------------------------------------
    def refresh_cameras(self) -> None:
        """Re-enumerate cameras, keeping the current selection if it survives."""
        previous = self.device_id()
        self._devices = enumerate_cameras()
        self._camera_list.clear()

        if not self._devices:
            # Non-selectable placeholder, so an empty list cannot be "accepted".
            item = QListWidgetItem("No cameras found")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._camera_list.addItem(item)
            return

        for index, name in self._devices:
            self._camera_list.addItem(f"{name}  [index {index}]")

        rows = [i for i, (index, _) in enumerate(self._devices) if index == previous]
        self._camera_list.setCurrentRow(rows[0] if rows else 0)

    def _selected_row(self) -> int:
        row = self._camera_list.currentRow()
        return row if 0 <= row < len(self._devices) else -1

    def _on_camera_selected(self) -> None:
        """Mirror the list selection into the manual index field.

        Keeping one source of truth (the spin box) means ``device_id()`` stays
        correct whichever way the user chose the device.
        """
        row = self._selected_row()
        if row >= 0:
            self._device.setValue(self._devices[row][0])

    def _on_double_click(self, _item: QListWidgetItem) -> None:
        if self._selected_row() >= 0:
            self.accept()

    # --- source mode ------------------------------------------------------
    def _set_camera_widgets_enabled(self, enabled: bool) -> None:
        self._camera_list.setEnabled(enabled)
        self._btn_refresh.setEnabled(enabled)

    def _pick_cam(self) -> None:
        self._use_camera = True
        self._btn_cam.setChecked(True)
        self._btn_vid.setChecked(False)
        self._set_camera_widgets_enabled(True)
        self._label_hint.setText(
            "Only cameras that actually delivered a frame are listed. Use the "
            "manual index if a device is missing from the list."
        )

    def _pick_vid(self) -> None:
        self._use_camera = False
        self._btn_vid.setChecked(True)
        self._btn_cam.setChecked(False)
        self._set_camera_widgets_enabled(False)
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

    # --- results ----------------------------------------------------------
    def is_camera(self) -> bool:
        return self._use_camera

    def device_id(self) -> int:
        return self._device.value()

    def device_name(self) -> str:
        """Display name of the selected camera, empty if it was typed manually."""
        row = self._selected_row()
        if row >= 0 and self._devices[row][0] == self.device_id():
            return self._devices[row][1]
        return ""

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
