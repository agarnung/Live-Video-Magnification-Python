"""Panel de opciones de magnificación (MagnifyOptions)."""

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


class MagnifyOptions(QWidget):
    """Controles equivalentes al widget de opciones del proyecto Qt original."""

    flags_changed = pyqtSignal(object)
    settings_changed = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._flags = ImageProcessingFlags()
        self._settings = ImageProcessingSettings()
        self._building = False

        root = QVBoxLayout(self)
        self._combo = QComboBox()
        self._combo.addItems(
            [
                "Sin magnificación",
                "Color (Euler)",
                "Movimiento (Laplace)",
                "Fase (Riesz)",
            ]
        )
        self._combo.currentIndexChanged.connect(self._on_mode_changed)
        root.addWidget(QLabel("Modo:"))
        root.addWidget(self._combo)

        self._gray = QCheckBox("Escala de grises")
        self._gray.toggled.connect(self._emit_flags)
        root.addWidget(self._gray)

        opt = QGroupBox("Parámetros")
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
            if isinstance(w, QSpinBox):
                w.valueChanged.connect(self._emit_settings)
            else:
                w.valueChanged.connect(self._emit_settings)

        form.addRow("Amplificación", self._amp)
        form.addRow("Longitud/corte (según modo)", self._wave)
        form.addRow("Banda baja", self._lo)
        form.addRow("Banda alta", self._hi)
        form.addRow("Atenuación cromática %", self._chrom)
        form.addRow("Niveles pirámide", self._levels)
        root.addWidget(opt)

        btn = QPushButton("Valores por defecto (modo actual)")
        btn.clicked.connect(self._reset_defaults)
        root.addWidget(btn)

        self._combo.setCurrentIndex(cfg.DEFAULT_MAGNIFY_TYPE)
        self._apply_mode_ui(0)
        self._reset_defaults()

    def _on_mode_changed(self, idx: int) -> None:
        self._apply_mode_ui(idx)
        self._reset_defaults()

    def _apply_mode_ui(self, idx: int) -> None:
        self._chrom.setVisible(idx == 2)
        if idx == 1:
            self._wave.setVisible(False)
            self._lo.setRange(0.0, 3.0)
            self._hi.setRange(0.0, 3.0)
        elif idx == 2:
            self._wave.setVisible(True)
            self._wave.setRange(0.0, 100.0)
            self._lo.setRange(0.0, 100.0)
            self._hi.setRange(0.0, 100.0)
        elif idx == 3:
            self._wave.setVisible(True)
            self._wave.setRange(0.0, 100.0)
            self._lo.setRange(0.0, 100.0)
            self._hi.setRange(0.0, 100.0)
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
