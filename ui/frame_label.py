"""Video label with a draggable ROI (FrameLabel)."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QMouseEvent, QPainter, QPixmap, QResizeEvent
from PyQt6.QtWidgets import QLabel, QMenu

from config import MIN_ROI_SIDE


class FrameLabel(QLabel):
    """Displays a QImage and lets the user drag a ROI rectangle."""

    roi_changed = pyqtSignal(QRect)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._full_image: QImage | None = None
        self._origin = QPoint()
        self._dragging = False
        self._rubber = QRect()
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 240)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._menu = QMenu(self)
        act_clear = self._menu.addAction("Restablecer ROI completo")
        act_clear.triggered.connect(self._clear_roi)

    def _show_context_menu(self, pos: QPoint) -> None:
        self._menu.exec(self.mapToGlobal(pos))

    def _clear_roi(self) -> None:
        """Full ROI = the whole captured frame, not the size of the QImage shown."""
        self._rubber = QRect()
        self._dragging = False
        self.update()
        self.roi_changed.emit(QRect(0, 0, 0, 0))

    def set_image(self, img: QImage) -> None:
        self._full_image = img.copy()
        self._refresh_pixmap()

    def _pixmap_rect(self) -> QRect | None:
        """Rectangle of the scaled pixmap, in widget coordinates."""
        pix = self.pixmap()
        if pix is None or pix.isNull() or self._full_image is None:
            return None
        off_x = (self.width() - pix.width()) // 2
        off_y = (self.height() - pix.height()) // 2
        return QRect(off_x, off_y, pix.width(), pix.height())

    def _refresh_pixmap(self) -> None:
        if self._full_image is None:
            return
        scaled = QPixmap.fromImage(self._full_image).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_pixmap()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.pixmap():
            self._dragging = True
            self._origin = event.position().toPoint()
            self._rubber = QRect(self._origin, self._origin)
            pr = self._pixmap_rect()
            if pr is not None:
                self._rubber = self._rubber.normalized().intersected(pr)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and self.pixmap():
            raw = QRect(self._origin, event.position().toPoint()).normalized()
            pr = self._pixmap_rect()
            if pr is not None:
                self._rubber = raw.intersected(pr)
            else:
                self._rubber = raw
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._emit_roi()
            self._rubber = QRect()
            self.update()
        super().mouseReleaseEvent(event)

    def _emit_roi(self) -> None:
        if self._full_image is None or not self.pixmap():
            return
        pr = self._pixmap_rect()
        pix = self.pixmap()
        if pix is None or pr is None:
            return
        rubber = self._rubber.normalized()
        rubber = rubber.intersected(pr)
        if rubber.width() < 4 or rubber.height() < 4:
            self.roi_changed.emit(QRect(0, 0, 0, 0))
            return
        rx = rubber.x() - pr.x()
        ry = rubber.y() - pr.y()
        rw = rubber.width()
        rh = rubber.height()
        sx = self._full_image.width() / pix.width()
        sy = self._full_image.height() / pix.height()
        ix = max(0, int(round(rx * sx)))
        iy = max(0, int(round(ry * sy)))
        iw = min(self._full_image.width() - ix, int(round(rw * sx)))
        ih = min(self._full_image.height() - iy, int(round(rh * sy)))
        if iw < MIN_ROI_SIDE or ih < MIN_ROI_SIDE:
            self.roi_changed.emit(QRect(0, 0, 0, 0))
            return
        self.roi_changed.emit(QRect(ix, iy, iw, ih))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._rubber.isNull() or not self.pixmap():
            return
        p = QPainter(self)
        p.setPen(Qt.GlobalColor.green)
        p.drawRect(self._rubber)
