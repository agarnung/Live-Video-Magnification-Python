"""Magnification options panel (MagnifyOptions)."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)

import config as cfg
from structures import ImageProcessingFlags, ImageProcessingSettings
from ui.widgets import RangeSlider, SegmentedControl


class MagnifyOptions(QWidget):
    """Controles equivalentes al widget de opciones del proyecto Qt original."""

    flags_changed = pyqtSignal(object)
    settings_changed = pyqtSignal(object)
    downscale_changed = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._flags = ImageProcessingFlags()
        self._settings = ImageProcessingSettings()
        self._building = False
        self._capture_fps = cfg.DEFAULT_CAPTURE_FPS

        root = QVBoxLayout(self)
        self._combo = QComboBox()
        self._combo.addItems(
            [
                "No magnification",
                "Colour (Eulerian)",
                "Motion (Laplacian)",
                "Phase (Riesz)",
            ]
        )
        self._combo.currentIndexChanged.connect(self._on_mode_changed)
        root.addWidget(QLabel("Mode:"))
        root.addWidget(self._combo)

        self._gray = QCheckBox("Grayscale")
        self._gray.toggled.connect(self._emit_flags)
        root.addWidget(self._gray)

        # Processing resolution. Shrinking the frame before the pyramid is the
        # cheapest way to buy frame rate: the cost falls quadratically.
        root.addWidget(QLabel("Processing resolution:"))
        self._scale = SegmentedControl(
            ["Full" if d == 1 else f"1/{d}" for d in cfg.PROCESSING_SCALES]
        )
        self._scale.currentIndexChanged.connect(
            lambda i: self.downscale_changed.emit(cfg.PROCESSING_SCALES[i])
        )
        root.addWidget(self._scale)

        opt = QGroupBox("Parameters")
        form = QFormLayout(opt)
        self._amp = QSpinBox()
        self._amp.setRange(0, 500)
        self._wave = QDoubleSpinBox()
        self._wave.setDecimals(2)
        self._wave.setRange(0.0, 10000.0)
        self._wave.setSingleStep(0.01)
        self._lo = QDoubleSpinBox()
        self._lo.setDecimals(3)
        self._lo.setSingleStep(0.001)
        self._hi = QDoubleSpinBox()
        self._hi.setDecimals(3)
        self._hi.setSingleStep(0.001)
        self._chrom = QSpinBox()
        self._chrom.setRange(0, 100)
        self._levels = QSpinBox()
        self._levels.setRange(1, 12)

        for w in (
            self._amp,
            self._wave,
            self._lo,
            self._hi,
            self._chrom,
            self._levels,
        ):
            w.valueChanged.connect(self._emit_settings)
        for w in (self._lo, self._hi):
            w.valueChanged.connect(lambda _=None: self._sync_band_slider())

        form.addRow("Amplification", self._amp)
        form.addRow("Wavelength / cutoff (mode-dependent)", self._wave)
        # A two-handle slider over the same pair of values. The spin boxes stay
        # for precise entry, but on their own they let the user set low > high,
        # which the filters cannot honour; the slider keeps the band ordered.
        self._band = RangeSlider()
        self._band.valuesChanged.connect(self._on_band_slider)
        form.addRow("Band (Hz)", self._band)
        form.addRow("Low cutoff (Hz)", self._lo)
        form.addRow("High cutoff (Hz)", self._hi)
        form.addRow("Chroma attenuation %", self._chrom)
        form.addRow("Pyramid levels", self._levels)
        root.addWidget(opt)

        btn = QPushButton("Reset to defaults (current mode)")
        btn.clicked.connect(self._reset_defaults)
        root.addWidget(btn)

        self._combo.setCurrentIndex(cfg.DEFAULT_MAGNIFY_TYPE)
        self._apply_mode_ui(0)
        self._reset_defaults()
        # The slider is built before the defaults are applied, so mirror them
        # once at the end or it would show an empty band.
        self._sync_band_slider()

    def _on_mode_changed(self, idx: int) -> None:
        self._apply_mode_ui(idx)
        self._reset_defaults()
        self._sync_band_slider()

    def set_capture_fps(self, fps: float) -> None:
        """
        Update the assumed capture rate and re-clamp the cutoff ranges.

        Every mode expresses its band in Hz, so no cutoff above Nyquist can
        mean anything. The previous fixed range of [0, 100] Hz let the user ask
        for a 90 Hz band on a 30 fps camera.
        """
        if fps and fps > 0:
            self._capture_fps = float(fps)
            self._apply_cutoff_ranges()

    def _apply_cutoff_ranges(self) -> None:
        nyquist = max(0.1, self._capture_fps / 2.0)
        for w in (self._lo, self._hi):
            w.setRange(0.05, nyquist)
        self._lo.setSuffix(" Hz")
        self._hi.setSuffix(" Hz")
        self._band.set_range(0.05, nyquist)
        self._band.set_step(0.01)
        self._band.set_values(self._lo.value(), self._hi.value())

    def _on_band_slider(self, low: float, high: float) -> None:
        """Slider moved: mirror into the spin boxes without echoing back."""
        if self._building:
            return
        self._building = True
        self._lo.setValue(low)
        self._hi.setValue(high)
        self._building = False
        self._emit_settings()

    def _sync_band_slider(self) -> None:
        """Spin box changed: mirror into the slider (silently)."""
        self._band.set_values(self._lo.value(), self._hi.value())

    def _apply_mode_ui(self, idx: int) -> None:
        self._chrom.setVisible(idx == 2)
        # All modes take their band in Hz, bounded by Nyquist.
        self._apply_cutoff_ranges()
        if idx == 1:
            self._wave.setVisible(False)
        elif idx == 2:
            self._wave.setVisible(True)
            self._wave.setRange(0.0, 100.0)
        elif idx == 3:
            self._wave.setVisible(True)
            self._wave.setRange(0.0, 100.0)
        else:
            self._wave.setVisible(False)

    def _reset_defaults(self) -> None:
        self._building = True
        idx = self._combo.currentIndex()
        if idx == 1:
            self._levels.setValue(cfg.DEFAULT_COL_MAG_LEVELS)
            self._amp.setValue(cfg.DEFAULT_CM_AMPLIFICATION)
            self._wave.setValue(cfg.DEFAULT_CM_COWAVELENGTH)
            self._lo.setValue(cfg.DEFAULT_CM_COLOW)
            self._hi.setValue(cfg.DEFAULT_CM_COHIGH)
            self._chrom.setValue(cfg.DEFAULT_CM_CHROMATTENUATION)
        elif idx == 2:
            self._levels.setValue(cfg.DEFAULT_LAP_MAG_LEVELS)
            self._amp.setValue(cfg.DEFAULT_MM_AMPLIFICATION)
            self._wave.setValue(cfg.DEFAULT_MM_COWAVELENGTH)
            self._lo.setValue(cfg.DEFAULT_MM_COLOW)
            self._hi.setValue(cfg.DEFAULT_MM_COHIGH)
            self._chrom.setValue(cfg.DEFAULT_MM_CHROMATTENUATION)
        elif idx == 3:
            self._levels.setValue(4)
            self._amp.setValue(cfg.DEFAULT_PB_AMPLIFICATION)
            self._wave.setValue(cfg.DEFAULT_PB_COWAVELENGTH)
            self._lo.setValue(cfg.DEFAULT_PB_COLOW)
            self._hi.setValue(cfg.DEFAULT_PB_COHIGH)
        self._building = False
        self._emit_flags()
        self._emit_settings()

    def _emit_flags(self) -> None:
        if self._building:
            return
        idx = self._combo.currentIndex()
        self._flags.grayscale_on = self._gray.isChecked()
        self._flags.color_magnify_on = idx == 1
        self._flags.laplace_magnify_on = idx == 2
        self._flags.riesz_magnify_on = idx == 3
        self.flags_changed.emit(self._flags)

    def _emit_settings(self) -> None:
        if self._building:
            return
        idx = self._combo.currentIndex()
        self._settings.amplification = float(self._amp.value())
        self._settings.levels = int(self._levels.value())
        if idx == 1:
            self._settings.co_wavelength = self._wave.value() * 10.0
            self._settings.co_low = self._lo.value()
            self._settings.co_high = self._hi.value()
            self._settings.chrom_attenuation = self._chrom.value() / 100.0
        elif idx == 2:
            self._settings.co_wavelength = self._wave.value() * 10.0
            self._settings.co_low = self._lo.value() / 100.0
            self._settings.co_high = self._hi.value() / 100.0
            self._settings.chrom_attenuation = self._chrom.value() / 100.0
        elif idx == 3:
            self._settings.co_wavelength = self._wave.value()
            self._settings.co_low = self._lo.value()
            self._settings.co_high = self._hi.value()
        self.settings_changed.emit(self._settings)

    def set_max_levels(self, max_lv: int) -> None:
        self._levels.setMaximum(max(1, max_lv))
        if self._levels.value() > max_lv:
            self._levels.setValue(max_lv)
            self._emit_settings()
