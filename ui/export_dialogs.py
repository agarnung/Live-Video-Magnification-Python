"""Export settings and progress dialogs (ExportSettingsDialog / ExportProgressDialog)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from exporter import ExportFormat, ExportRequest, SplitMode
from structures import ImageProcessingFlags, ImageProcessingSettings


@dataclass
class ExportSeed:
    """
    Live state the dialog is pre-filled from.

    Pre-filling matters more than it looks: the magnification parameters are the
    ones the user just tuned by eye in the preview, so re-typing them would be
    both tedious and a source of "the export looks different" bug reports.
    """

    source_path: str = ""
    settings: ImageProcessingSettings = field(default_factory=ImageProcessingSettings)
    flags: ImageProcessingFlags = field(default_factory=ImageProcessingFlags)
    roi: tuple[int, int, int, int] = (0, 0, 0, 0)
    downscale: int = 1
    capture_fps: float = 30.0
    frame_count: int = 0
    max_levels: int = 0


class ExportSettingsDialog(QDialog):
    """Modal export settings, pre-filled with the processing panel's current values."""

    def __init__(self, seed: ExportSeed, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export video")
        self._seed = seed
        self._request: ExportRequest | None = None

        root = QVBoxLayout(self)

        # ---- output -------------------------------------------------------
        out_box = QGroupBox("Output")
        out_form = QFormLayout(out_box)
        self._format = QComboBox()
        for fmt in ExportFormat:
            self._format.addItem(fmt.label, fmt)
        self._format.currentIndexChanged.connect(self._on_format_changed)
        out_form.addRow("Format", self._format)

        self._path = QLineEdit(self._suggest_path(ExportFormat.MP4_H264))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self._path, 1)
        path_row.addWidget(browse)
        out_form.addRow("File", path_row)

        self._file_fps = QDoubleSpinBox()
        self._file_fps.setRange(1.0, 1000.0)
        self._file_fps.setDecimals(2)
        self._file_fps.setValue(max(1.0, seed.capture_fps))
        self._file_fps.setSuffix(" fps")
        out_form.addRow("File frame rate", self._file_fps)

        self._capture_fps = QDoubleSpinBox()
        self._capture_fps.setRange(1.0, 10000.0)
        self._capture_fps.setDecimals(2)
        self._capture_fps.setValue(max(1.0, seed.capture_fps))
        self._capture_fps.setSuffix(" fps")
        self._capture_fps.setToolTip(
            "Rate the algorithm assumes. It sets the temporal filter cutoffs, so "
            "it must match the true rate of the source; the file frame rate above "
            "only changes playback cadence."
        )
        out_form.addRow("Capture frame rate", self._capture_fps)
        root.addWidget(out_box)

        # ---- composition ---------------------------------------------------
        comp_box = QGroupBox("Composition")
        comp_form = QFormLayout(comp_box)
        self._split = QComboBox()
        self._split.addItem("Processed only", SplitMode.NONE)
        self._split.addItem("Original | Processed (left/right)", SplitMode.LEFT_RIGHT)
        self._split.addItem("Original / Processed (top/bottom)", SplitMode.TOP_BOTTOM)
        self._split.currentIndexChanged.connect(self._sync_overlay_enabled)
        comp_form.addRow("Layout", self._split)

        self._overlay = QCheckBox("Burn in “Original” / “Processed” labels")
        comp_form.addRow("", self._overlay)
        root.addWidget(comp_box)

        # ---- processing ----------------------------------------------------
        proc_box = QGroupBox("Processing")
        proc_form = QFormLayout(proc_box)
        self._mode = QComboBox()
        self._mode.addItems(
            [
                "No magnification",
                "Colour (Eulerian)",
                "Motion (Laplacian)",
                "Phase (Riesz)",
            ]
        )
        self._mode.setCurrentIndex(self._mode_index_from(seed.flags))
        proc_form.addRow("Mode", self._mode)

        self._amp = QDoubleSpinBox()
        self._amp.setRange(0.0, 500.0)
        self._amp.setValue(seed.settings.amplification)
        proc_form.addRow("Amplification", self._amp)

        self._levels = QSpinBox()
        self._levels.setRange(1, max(1, seed.max_levels or 12))
        self._levels.setValue(max(1, seed.settings.levels))
        proc_form.addRow("Pyramid levels", self._levels)

        self._lo = QDoubleSpinBox()
        self._lo.setDecimals(3)
        self._lo.setRange(0.0, 10000.0)
        self._lo.setValue(seed.settings.co_low)
        proc_form.addRow("Low cutoff", self._lo)

        self._hi = QDoubleSpinBox()
        self._hi.setDecimals(3)
        self._hi.setRange(0.0, 10000.0)
        self._hi.setValue(seed.settings.co_high)
        proc_form.addRow("High cutoff", self._hi)

        self._downscale = QComboBox()
        for d in (1, 2, 4, 8):
            self._downscale.addItem("Full" if d == 1 else f"1/{d}", d)
        idx = self._downscale.findData(seed.downscale)
        self._downscale.setCurrentIndex(max(0, idx))
        proc_form.addRow("Processing resolution", self._downscale)

        self._grayscale = QCheckBox("Grayscale")
        self._grayscale.setChecked(seed.flags.grayscale_on)
        proc_form.addRow("", self._grayscale)

        self._use_roi = QCheckBox("Restrict to the selected ROI")
        has_roi = seed.roi[2] > 0 and seed.roi[3] > 0
        self._use_roi.setEnabled(has_roi)
        self._use_roi.setChecked(has_roi)
        proc_form.addRow("", self._use_roi)
        root.addWidget(proc_box)

        # ---- range ---------------------------------------------------------
        total = max(0, seed.frame_count)
        range_box = QGroupBox("Range")
        range_form = QFormLayout(range_box)
        self._start = QSpinBox()
        self._start.setRange(0, max(0, total - 1))
        self._end = QSpinBox()
        # The range is [start, end): `end` may equal the frame count.
        self._end.setRange(1, max(1, total))
        self._end.setValue(max(1, total))
        self._start.valueChanged.connect(self._clamp_range)
        self._end.valueChanged.connect(self._clamp_range)
        range_form.addRow("Start frame (inclusive)", self._start)
        range_form.addRow("End frame (exclusive)", self._end)
        range_form.addRow("", QLabel(f"Source has {total} frames."))
        range_box.setEnabled(total > 0)
        root.addWidget(range_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._sync_overlay_enabled()

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _mode_index_from(flags: ImageProcessingFlags) -> int:
        if flags.color_magnify_on:
            return 1
        if flags.laplace_magnify_on:
            return 2
        if flags.riesz_magnify_on:
            return 3
        return 0

    def _suggest_path(self, fmt: ExportFormat) -> str:
        stem = Path(self._seed.source_path).stem or "export"
        parent = Path(self._seed.source_path).parent
        return str(parent / f"{stem}_magnified.{fmt.extension}")

    def _on_format_changed(self) -> None:
        """Follow the format with the extension, but keep a path the user edited."""
        fmt: ExportFormat = self._format.currentData()
        current = Path(self._path.text())
        if current.name:
            self._path.setText(str(current.with_suffix("." + fmt.extension)))
        else:
            self._path.setText(self._suggest_path(fmt))

    def _browse(self) -> None:
        fmt: ExportFormat = self._format.currentData()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export to",
            self._path.text(),
            f"{fmt.label} (*.{fmt.extension})",
        )
        if path:
            self._path.setText(path)

    def _sync_overlay_enabled(self) -> None:
        """Labels only make sense when there are two panes to tell apart."""
        split: SplitMode = self._split.currentData()
        on = split is not SplitMode.NONE
        self._overlay.setEnabled(on)
        if on and not self._overlay.isChecked():
            self._overlay.setChecked(True)
        if not on:
            self._overlay.setChecked(False)

    def _clamp_range(self) -> None:
        """Keep the half-open range non-empty without fighting the user's typing."""
        if self._end.value() <= self._start.value():
            sender = self.sender()
            if sender is self._start:
                self._end.setValue(self._start.value() + 1)
            else:
                self._start.setValue(self._end.value() - 1)

    # ------------------------------------------------------------------ result

    def request(self) -> ExportRequest | None:
        """The assembled request; valid after ``exec() == Accepted``."""
        return self._request

    def accept(self) -> None:
        idx = self._mode.currentIndex()
        flags = ImageProcessingFlags(
            grayscale_on=self._grayscale.isChecked(),
            color_magnify_on=idx == 1,
            laplace_magnify_on=idx == 2,
            riesz_magnify_on=idx == 3,
        )
        settings = ImageProcessingSettings(
            amplification=self._amp.value(),
            co_wavelength=self._seed.settings.co_wavelength,
            co_low=self._lo.value(),
            co_high=self._hi.value(),
            chrom_attenuation=self._seed.settings.chrom_attenuation,
            levels=self._levels.value(),
            framerate=self._capture_fps.value(),
        )
        self._request = ExportRequest(
            output_path=self._path.text(),
            source_path=self._seed.source_path,
            settings=settings,
            flags=flags,
            roi=self._seed.roi if self._use_roi.isChecked() else (0, 0, 0, 0),
            downscale=self._downscale.currentData(),
            capture_fps=self._capture_fps.value(),
            file_fps=self._file_fps.value(),
            split=self._split.currentData(),
            text_overlay=self._overlay.isChecked(),
            fmt=self._format.currentData(),
            start_frame=self._start.value(),
            end_frame=self._end.value() if self._seed.frame_count > 0 else -1,
        )
        super().accept()


class ExportProgressDialog(QDialog):
    """
    Modal progress view driven by the owner on a timer.

    It owns no threads and never touches the Exporter directly; closing the
    window counts as an abort request, which the owner forwards. Keeping the
    dialog passive means a crash in the encoder cannot leave the GUI wedged
    inside a callback.
    """

    aborted = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Exporting…")
        self.setModal(True)
        self._finished = False
        self._emitted = False

        root = QVBoxLayout(self)
        self._label = QLabel("Starting…")
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)  # indeterminate until a total is known
        root.addWidget(self._bar)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)
        self.resize(420, 130)

    def set_progress(self, done: int, total: int, note: str = "") -> None:
        """``total < 0`` means unknown, which the bar shows as a busy indicator."""
        if total is not None and total >= 0:
            self._bar.setRange(0, max(1, total))
            self._bar.setValue(min(done, max(1, total)))
            self._label.setText(f"{note}  {done} / {total} frames".strip())
        else:
            self._bar.setRange(0, 0)
            self._label.setText(f"{note}  {done} frames".strip())

    def mark_finished(self, message: str) -> None:
        """Stop treating a later close() as an abort, and switch Cancel to Close."""
        self._finished = True
        self._label.setText(message)
        self._buttons.setStandardButtons(QDialogButtonBox.StandardButton.Close)
        self._buttons.rejected.connect(self.accept)
        self._buttons.accepted.connect(self.accept)

    def reject(self) -> None:
        if self._finished:
            super().accept()
            return
        if not self._emitted:
            self._emitted = True
            self.aborted.emit()
        self._label.setText("Cancelling…")
