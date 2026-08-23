"""
Custom widgets ported from the C++ reference UI.

Three controls that Qt does not provide and the reference implementation draws
by hand: a two-handle range slider, a segmented control and an animated toggle
switch.  All of them paint themselves, so they follow whatever palette the
active theme installs.
"""

from __future__ import annotations

import math
from typing import Sequence

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QAbstractButton, QSizePolicy, QWidget


# ---------------------------------------------------------------------------
# RangeSlider
# ---------------------------------------------------------------------------

class RangeSlider(QWidget):
    """
    Two-handle slider bounding a ``[low, high]`` range.

    Values snap to a step and the handles cannot cross, which is what makes it
    the right control for a frequency band: two independent spin boxes let the
    user set ``low > high``, a state the filters cannot honour.

    ``valuesChanged`` fires only for user interaction; :meth:`set_values` is
    silent, so restoring saved state does not echo back as a change.
    """

    valuesChanged = pyqtSignal(float, float)

    _HANDLE_R = 7
    _TRACK_H = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min = 0.0
        self._max = 1.0
        self._step = 0.01
        self._low = 0.0
        self._high = 1.0
        self._log = False
        self._drag = None          # None | "low" | "high"
        self._hover = None
        self._active = "low"       # handle the keyboard drives
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # -- configuration ---------------------------------------------------

    def set_range(self, lo: float, hi: float) -> None:
        """Set the value range, clamping the current values into it."""
        if hi <= lo:
            hi = lo + 1e-6
        self._min, self._max = float(lo), float(hi)
        self._low = min(max(self._low, self._min), self._max)
        self._high = min(max(self._high, self._low), self._max)
        self.update()

    def set_step(self, step: float) -> None:
        self._step = max(float(step), 1e-9)

    def set_log_scale(self, on: bool) -> None:
        """
        Map pixels logarithmically while values and step stay linear.

        Useful for frequency bands, where the interesting range spans decades.
        Requires a strictly positive minimum.
        """
        self._log = bool(on) and self._min > 0.0
        self.update()

    def set_values(self, low: float, high: float) -> None:
        """Set both values without emitting ``valuesChanged``."""
        low, high = float(low), float(high)
        if high < low:
            low, high = high, low
        self._low = min(max(self._snap(low), self._min), self._max)
        self._high = min(max(self._snap(high), self._low), self._max)
        self.update()

    def low_value(self) -> float:
        return self._low

    def high_value(self) -> float:
        return self._high

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(180, 2 * self._HANDLE_R + 10)

    # -- geometry --------------------------------------------------------

    def _track(self) -> QRect:
        m = self._HANDLE_R + 1
        y = (self.height() - self._TRACK_H) // 2
        return QRect(m, y, max(1, self.width() - 2 * m), self._TRACK_H)

    def _snap(self, v: float) -> float:
        n = round((v - self._min) / self._step)
        return self._min + n * self._step

    def _value_to_x(self, v: float) -> int:
        t = self._track()
        span = self._max - self._min
        if span <= 0:
            return t.left()
        if self._log:
            lo, hi, vv = math.log(self._min), math.log(self._max), math.log(max(v, self._min))
            frac = (vv - lo) / (hi - lo) if hi > lo else 0.0
        else:
            frac = (v - self._min) / span
        return int(t.left() + frac * t.width())

    def _x_to_value(self, x: int) -> float:
        t = self._track()
        frac = 0.0 if t.width() <= 0 else (x - t.left()) / t.width()
        frac = min(max(frac, 0.0), 1.0)
        if self._log:
            lo, hi = math.log(self._min), math.log(self._max)
            return math.exp(lo + frac * (hi - lo))
        return self._min + frac * (self._max - self._min)

    # -- interaction -----------------------------------------------------

    def _nearest(self, x: int) -> str:
        return "low" if abs(x - self._value_to_x(self._low)) <= abs(
            x - self._value_to_x(self._high)
        ) else "high"

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._drag = self._active = self._nearest(e.position().toPoint().x())
        self._apply_from_x(e.position().toPoint().x())

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        x = e.position().toPoint().x()
        if self._drag:
            self._apply_from_x(x)
        else:
            h = self._nearest(x)
            if h != self._hover:
                self._hover = h
                self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._drag = None

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = None
        self.update()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        d = 0
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            d = -1
        elif e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            d = +1
        elif e.key() == Qt.Key.Key_Tab:
            self._active = "high" if self._active == "low" else "low"
            self.update()
            return
        if d:
            if self._active == "low":
                self._set_low(self._low + d * self._step, emit=True)
            else:
                self._set_high(self._high + d * self._step, emit=True)
        else:
            super().keyPressEvent(e)

    def _apply_from_x(self, x: int) -> None:
        v = self._snap(self._x_to_value(x))
        if self._drag == "low":
            self._set_low(v, emit=True)
        else:
            self._set_high(v, emit=True)

    def _set_low(self, v: float, emit: bool) -> None:
        # Handles must not cross: keep one step of separation.
        v = min(max(v, self._min), self._high - self._step)
        if abs(v - self._low) > 1e-12:
            self._low = v
            self.update()
            if emit:
                self.valuesChanged.emit(self._low, self._high)

    def _set_high(self, v: float, emit: bool) -> None:
        v = min(max(v, self._low + self._step), self._max)
        if abs(v - self._high) > 1e-12:
            self._high = v
            self.update()
            if emit:
                self.valuesChanged.emit(self._low, self._high)

    # -- painting --------------------------------------------------------

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        t = self._track()
        radius = self._TRACK_H / 2

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(pal.mid())
        p.drawRoundedRect(t, radius, radius)

        x0, x1 = self._value_to_x(self._low), self._value_to_x(self._high)
        p.setBrush(pal.highlight())
        p.drawRoundedRect(QRect(x0, t.top(), max(1, x1 - x0), t.height()), radius, radius)

        for name, x in (("low", x0), ("high", x1)):
            r = self._HANDLE_R + (1 if name in (self._hover, self._drag) else 0)
            p.setBrush(pal.base())
            p.setPen(QPen(pal.highlight().color(),
                          2.0 if name == self._active and self.hasFocus() else 1.2))
            p.drawEllipse(QPoint(x, t.center().y() + 1), r, r)


# ---------------------------------------------------------------------------
# SegmentedControl
# ---------------------------------------------------------------------------

class SegmentedControl(QWidget):
    """
    Horizontal pill of mutually exclusive segments.

    Intended for small closed sets such as the 1/1..1/8 processing divisors,
    where a combo box hides the options behind a click.
    """

    currentIndexChanged = pyqtSignal(int)

    def __init__(self, labels: Sequence[str] = (), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._labels = list(labels)
        self._index = 0
        self._hover = -1
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_labels(self, labels: Sequence[str]) -> None:
        self._labels = list(labels)
        self._index = min(self._index, max(0, len(self._labels) - 1))
        self.updateGeometry()
        self.update()

    def current_index(self) -> int:
        return self._index

    def set_current_index(self, i: int, emit: bool = False) -> None:
        i = min(max(int(i), 0), max(0, len(self._labels) - 1))
        if i != self._index:
            self._index = i
            self.update()
            if emit:
                self.currentIndexChanged.emit(i)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self.font())
        w = sum(fm.horizontalAdvance(t) + 24 for t in self._labels) or 120
        return QSize(w, fm.height() + 14)

    def _seg_at(self, x: int) -> int:
        if not self._labels:
            return -1
        w = self.width() / len(self._labels)
        return min(int(x / w), len(self._labels) - 1)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.set_current_index(self._seg_at(e.position().toPoint().x()), emit=True)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        h = self._seg_at(e.position().toPoint().x())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = -1
        self.update()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Left:
            self.set_current_index(self._index - 1, emit=True)
        elif e.key() == Qt.Key.Key_Right:
            self.set_current_index(self._index + 1, emit=True)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e) -> None:  # noqa: N802
        if not self._labels:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        r = self.rect().adjusted(0, 0, -1, -1)
        radius = r.height() / 2

        path = QPainterPath()
        path.addRoundedRect(float(r.x()), float(r.y()), float(r.width()), float(r.height()),
                            radius, radius)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(pal.mid())
        p.drawPath(path)

        seg_w = r.width() / len(self._labels)
        for i, text in enumerate(self._labels):
            cell = QRect(int(r.x() + i * seg_w), r.y(), int(seg_w), r.height())
            if i == self._index:
                p.setBrush(pal.highlight())
                sub = QPainterPath()
                sub.addRoundedRect(float(cell.x() + 2), float(cell.y() + 2),
                                   float(cell.width() - 4), float(cell.height() - 4),
                                   radius - 2, radius - 2)
                p.drawPath(sub)
                p.setPen(pal.highlightedText().color())
            elif i == self._hover:
                p.setPen(pal.text().color())
            else:
                p.setPen(pal.windowText().color())
            p.drawText(cell, int(Qt.AlignmentFlag.AlignCenter), text)


# ---------------------------------------------------------------------------
# ToggleSwitch
# ---------------------------------------------------------------------------

class ToggleSwitch(QAbstractButton):
    """
    Checkable switch with a sliding knob.

    Reads more clearly than a check box for settings that are conceptually
    on/off rather than "selected", and the animation makes the state change
    legible when it also triggers a pipeline reset.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"position", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self._animate)

    def _get_position(self) -> float:
        return self._pos

    def _set_position(self, v: float) -> None:
        self._pos = float(v)
        self.update()

    position = pyqtProperty(float, fget=_get_position, fset=_set_position)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(42, 22)

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        r = self.rect().adjusted(1, 1, -1, -1)
        radius = r.height() / 2

        off, on = pal.mid().color(), pal.highlight().color()
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, radius, radius)

        knob_r = r.height() / 2 - 2
        cx = r.left() + knob_r + 2 + self._pos * (r.width() - 2 * knob_r - 4)
        p.setBrush(pal.base())
        p.drawEllipse(QPoint(int(cx), r.center().y() + 1), int(knob_r), int(knob_r))
