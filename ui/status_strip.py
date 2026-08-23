"""
Bottom status strip, plus the Qt-free stats -> health mapping it renders.

The health rule deliberately treats the two source kinds differently:

* A **file** is judged by achieved fps against its target playback cadence.  A
  file cannot shed frames (the reader blocks), so falling behind shows up as a
  slower-than-target rate and nowhere else.
* A **camera** is judged by the fraction of frames it sheds.  A camera hands us
  frames whether or not we are ready, so the honest measure of "keeping up" is
  how many we had to throw away -- comparing the rate to CAP_PROP_FPS would
  flatter us, because the shed frames simply never enter the average.

The strip is refreshed from a QTimer poll, never from a per-frame signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from instrumentation import StatsSnapshot
from ui.theme import Metrics, mono_font


class Health(Enum):
    """Severity levels the status dot can show."""

    IDLE = "idle"
    OK = "ok"
    WARN = "warn"
    BAD = "bad"


# File thresholds: achieved/target ratio.
SPEED_OK = 0.95
SPEED_WARN = 0.80
# Camera thresholds: fraction of frames shed before processing.
DROP_WARN = 0.02
DROP_BAD = 0.15

FALLING_BEHIND_HINT = "Falling behind - shrink the ROI or increase downscale"


@dataclass(frozen=True)
class HealthInputs:
    """Everything the health rule needs, with no Qt types involved."""

    live: bool = False  # a source is open AND frames are flowing
    camera: bool = False  # True = live camera, False = file
    fps: float = 0.0  # processed fps (EMA)
    target_fps: float = 0.0  # file playback target (0 = unknown)
    drop_fraction: float = 0.0  # EMA share of frames shed (0 for a file)


def drop_health(fraction: float) -> Health:
    """Severity of a camera's shed-share."""
    if fraction < DROP_WARN:
        return Health.OK
    if fraction < DROP_BAD:
        return Health.WARN
    return Health.BAD


def speed_health(inputs: HealthInputs) -> Health:
    """A camera keeps up iff it is not shedding; a file is judged on cadence."""
    if not inputs.live:
        return Health.IDLE
    if inputs.camera:
        return drop_health(inputs.drop_fraction)
    if inputs.target_fps <= 0.0:
        return Health.OK
    ratio = inputs.fps / inputs.target_fps
    if ratio >= SPEED_OK:
        return Health.OK
    return Health.WARN if ratio >= SPEED_WARN else Health.BAD


def _apply_state(widget: QWidget, health: Health) -> None:
    """
    Push the health onto the widget's `state` property and repolish.

    Qt caches the resolved stylesheet per widget, so a property change is only
    picked up after an explicit unpolish/polish round.
    """
    if widget.property("state") == health.value:
        return
    widget.setProperty("state", health.value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


class StatusStrip(QWidget):
    """
    One-line pipeline readout: caption, health dot, value, playback spinbox.

    Numbers use a monospace face so the digits keep a fixed advance width and
    the row does not jitter as the rate changes.
    """

    playback_fps_changed = pyqtSignal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        caption_font = QFont(self.font())
        if caption_font.pointSizeF() > 0:
            caption_font.setPointSizeF(caption_font.pointSizeF() * 0.80)
        caption_font.setCapitalization(QFont.Capitalization.AllUppercase)
        caption_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106.0)
        caption_font.setWeight(QFont.Weight.DemiBold)

        value_font = mono_font(self.font())
        value_font.setWeight(QFont.Weight.Medium)

        row = QHBoxLayout(self)
        row.setContentsMargins(Metrics.SPACE4, Metrics.SPACE1, Metrics.SPACE4, Metrics.SPACE1)
        row.setSpacing(6)

        caption = QLabel("Fps", self)
        caption.setObjectName("statCaption")
        caption.setFont(caption_font)
        row.addWidget(caption)

        self._dot = QLabel(self)
        self._dot.setObjectName("statDot")
        row.addWidget(self._dot)

        self._value = QLabel(self)
        self._value.setObjectName("statValue")
        self._value.setFont(value_font)
        row.addWidget(self._value)

        self._slash = QLabel("/", self)
        self._slash.setObjectName("statSlash")
        row.addWidget(self._slash)

        self._playback_spin = QDoubleSpinBox(self)
        self._playback_spin.setObjectName("statSpin")
        self._playback_spin.setRange(1.0, 999.99)
        self._playback_spin.setDecimals(2)
        self._playback_spin.setSingleStep(1.0)
        self._playback_spin.setValue(30.0)
        # Apply on Enter/focus-out rather than per keystroke: typing "120" would
        # otherwise re-time the source at 1 fps then 12 fps on the way.
        self._playback_spin.setKeyboardTracking(False)
        self._playback_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._playback_spin.setFont(value_font)
        self._playback_spin.setToolTip(
            "Playback speed - the cadence the clip plays back at. Separate from "
            "the capture FPS used for the Hz maths in the magnification panel."
        )
        self._playback_spin.setFixedWidth(
            QFontMetrics(value_font).horizontalAdvance("999.99") + 18
        )
        self._playback_spin.valueChanged.connect(self.playback_fps_changed)
        row.addWidget(self._playback_spin)

        # A camera is never re-timed, so its rate is shown read-only.
        self._reported = QLabel(self)
        self._reported.setObjectName("statValue")
        self._reported.setFont(value_font)
        self._reported.setToolTip("The frame rate the camera reports delivering.")
        row.addWidget(self._reported)

        self._detail = QLabel(self)
        self._detail.setObjectName("statValue")
        self._detail.setFont(value_font)
        row.addSpacing(Metrics.SPACE4)
        row.addWidget(self._detail)

        row.addStretch(1)

        self._hint = QLabel(self)
        self._hint.setObjectName("statHint")
        self._hint.setVisible(False)
        row.addWidget(self._hint)

        self.set_stats(StatsSnapshot(), 0.0, False, False)

    def set_stats(
        self,
        s: StatsSnapshot,
        target_fps: float,
        has_source: bool,
        camera_source: bool,
    ) -> None:
        """Render a polled snapshot. Cheap enough to call at a few Hz."""
        # Paused or just-opened reads as idle, not live.
        live = has_source and s.fps > 0.05

        health = speed_health(
            HealthInputs(
                live=live,
                camera=camera_source,
                fps=s.fps,
                target_fps=target_fps,
                drop_fraction=s.drop_fraction,
            )
        )

        self._value.setText(f"{min(s.fps, 999.9):.1f}" if live else "—")
        _apply_state(self._dot, health)
        _apply_state(self._value, health)

        show_input = has_source and not camera_source
        show_reported = has_source and camera_source and target_fps > 0.0
        self._playback_spin.setVisible(show_input)
        self._reported.setVisible(show_reported)
        self._slash.setVisible(show_input or show_reported)
        if show_reported:
            self._reported.setText(f"{min(target_fps, 999.9):.1f}")

        self._detail.setText(
            f"lat {s.latency_mean_ms:5.1f}/{s.latency_p95_ms:5.1f} ms   "
            f"q {s.queue_depth}   drop {s.drop_fraction * 100.0:4.1f}%   "
            f"err {s.proc_errors + s.read_errors}"
        )

        if health is Health.BAD:
            self._hint.setText(FALLING_BEHIND_HINT)
            self._hint.setVisible(True)
        else:
            self._hint.setVisible(False)

    def set_playback_fps(self, fps: float) -> None:
        """Set the spinbox without echoing a change back to the pipeline."""
        if fps <= 0.0:
            return
        self._playback_spin.blockSignals(True)
        try:
            self._playback_spin.setValue(min(fps, 999.99))
        finally:
            self._playback_spin.blockSignals(False)
