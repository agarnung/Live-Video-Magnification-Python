"""Frame-domain scrub timeline with draggable playhead and IN/OUT handles."""

from __future__ import annotations

import math
from enum import Enum, auto

from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
from PyQt6.QtWidgets import QWidget

_TIME_TEXT_W = 120  # width reserved for the "current / total" label, px
_MARGIN = 10
_HIT_PX = 9  # grab radius for the in/out handles, px


def _format_time(seconds: float) -> str:
    """Render seconds as m:ss (or h:mm:ss), tolerating NaN/negative input."""
    if not math.isfinite(seconds) or seconds < 0.0:
        seconds = 0.0
    total = int(seconds)
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


class _Drag(Enum):
    """Which element the current mouse drag owns."""

    NONE = auto()
    PLAYHEAD = auto()
    IN = auto()
    OUT = auto()


class TimelineView(QWidget):
    """A scrub bar whose domain is *frames*, not seconds.

    Frames are the only unit the decoder can seek exactly, so the widget stores
    and emits frame indices; the clock label is derived from
    ``frame / playback_fps``. That also means the label follows the *playback*
    rate, so slowing playback down stretches the displayed duration honestly
    instead of showing the file's native timing.

    The active range is ``[in, out)`` with an exclusive out-point, mirroring the
    decoder's own convention so no off-by-one translation is needed anywhere.
    """

    seek_requested = pyqtSignal(int)
    scrub_started = pyqtSignal()
    scrub_finished = pyqtSignal()
    in_out_changed = pyqtSignal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(34)
        self._total = 0
        self._playhead = 0
        self._in = 0
        self._out = 0  # exclusive; == _total when the whole clip is selected
        self._fps = 30.0
        self._drag = _Drag.NONE

    def sizeHint(self) -> QSize:
        return QSize(400, 34)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def set_frame_count(self, total: int) -> None:
        """Set the clip length; ``<= 0`` disables interaction entirely."""
        self._total = max(0, int(total))
        if self._out <= 0 or self._out > self._total:
            self._out = self._total
        self._in = min(max(self._in, 0), max(0, self._out - 1))
        self._playhead = self._clamp_to_range(self._playhead)
        self.update()

    def set_playhead_frame(self, frame: int) -> None:
        """Follow playback; ignored mid-drag so the handle does not fight back."""
        if self._drag is _Drag.PLAYHEAD:
            return
        self._playhead = min(max(int(frame), 0), max(0, self._total))
        self.update()

    def set_fps(self, fps: float) -> None:
        self._fps = float(fps)
        self.update()

    def set_in_out(self, in_frame: int, out_frame: int) -> None:
        """Set the active range; ``out_frame < 0`` means "to the end"."""
        if self._total <= 0:
            self._in = self._out = 0
            self.update()
            return
        self._out = self._total if out_frame < 0 else min(max(int(out_frame), 1), self._total)
        self._in = min(max(int(in_frame), 0), self._out - 1)
        self._playhead = self._clamp_to_range(self._playhead)
        self.update()

    def reset_to_start(self) -> None:
        """Snap the playhead to the in-point (the clip's "start" after a trim)."""
        if self._drag is _Drag.PLAYHEAD:
            return
        self._playhead = self._in
        self.update()

    def frame_count(self) -> int:
        return self._total

    def in_frame(self) -> int:
        return self._in

    def out_frame(self) -> int:
        """The exclusive out-point."""
        return self._out

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def _track_left(self) -> float:
        return float(_MARGIN)

    def _track_right(self) -> float:
        return float(self.width() - _TIME_TEXT_W - _MARGIN)

    def _frame_to_x(self, frame: int) -> int:
        frac = min(max(frame / float(max(1, self._total)), 0.0), 1.0)
        return round(self._track_left() + frac * (self._track_right() - self._track_left()))

    def _x_to_frame(self, x: float) -> int:
        span = self._track_right() - self._track_left()
        if span <= 0.0:
            return 0
        frac = min(max((x - self._track_left()) / span, 0.0), 1.0)
        return round(frac * self._total)

    def _clamp_to_range(self, frame: int) -> int:
        """Confine a playhead frame to ``[in, out - 1]`` (out is exclusive)."""
        if self._total <= 0:
            return 0
        return min(max(frame, self._in), max(self._in, self._out - 1))

    def _time_text(self) -> str:
        fps = self._fps if self._fps > 0.0 else 30.0
        return f"{_format_time(self._playhead / fps)} / {_format_time(self._total / fps)}"

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = self.palette()
        left, right = self._track_left(), self._track_right()
        cy = self.height() // 2

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pal.color(pal.ColorRole.Mid))
        painter.drawRoundedRect(QRectF(left, cy - 2, max(0.0, right - left), 4), 2, 2)

        if self._total > 0:
            in_x, out_x = self._frame_to_x(self._in), self._frame_to_x(self._out)
            painter.setBrush(pal.color(pal.ColorRole.Highlight))
            painter.drawRoundedRect(QRectF(in_x, cy - 2, max(0, out_x - in_x), 4), 2, 2)
            # Handles are drawn taller than the groove so they stay grabbable.
            painter.drawRect(QRectF(in_x - 2, cy - 9, 4, 18))
            painter.drawRect(QRectF(out_x - 2, cy - 9, 4, 18))

            px = self._frame_to_x(self._playhead)
            painter.setBrush(pal.color(pal.ColorRole.Text))
            painter.drawRect(QRectF(px - 1, cy - 11, 2, 22))
            painter.drawEllipse(QPointF(px, cy), 5, 5)

        color = QColor(pal.color(pal.ColorRole.WindowText))
        color.setAlphaF(0.7)
        painter.setPen(color)
        painter.drawText(
            QRect(int(right) + _MARGIN, 0, _TIME_TEXT_W - _MARGIN, self.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
            self._time_text(),
        )

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._total <= 0 or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        x = event.position().x()
        in_x, out_x = self._frame_to_x(self._in), self._frame_to_x(self._out)
        # The handles win over the playhead: they are the smaller targets, and a
        # mis-grabbed trim handle is far more annoying than a mis-placed seek.
        if abs(x - in_x) <= _HIT_PX:
            self._drag = _Drag.IN
        elif abs(x - out_x) <= _HIT_PX:
            self._drag = _Drag.OUT
        else:
            self._drag = _Drag.PLAYHEAD
            self._playhead = self._clamp_to_range(self._x_to_frame(x))
            self.scrub_started.emit()
            self.seek_requested.emit(self._playhead)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag is _Drag.NONE:
            return
        x = event.position().x()
        if self._drag is _Drag.PLAYHEAD:
            self._playhead = self._clamp_to_range(self._x_to_frame(x))
            self.seek_requested.emit(self._playhead)
        elif self._drag is _Drag.IN:
            # Handles must never cross: IN stops one frame short of OUT.
            self._in = min(max(self._x_to_frame(x), 0), max(0, self._out - 1))
            self.in_out_changed.emit(self._in, self._out)
        elif self._drag is _Drag.OUT:
            self._out = min(max(self._x_to_frame(x), self._in + 1), self._total)
            self.in_out_changed.emit(self._in, self._out)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        was, self._drag = self._drag, _Drag.NONE
        if was is _Drag.PLAYHEAD:
            self.scrub_finished.emit()
        elif was in (_Drag.IN, _Drag.OUT):
            # Moving the range may have left the playhead outside it.
            clamped = self._clamp_to_range(self._playhead)
            if clamped != self._playhead:
                self._playhead = clamped
                self.seek_requested.emit(self._playhead)
                self.update()
