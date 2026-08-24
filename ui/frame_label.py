"""Video display with four comparison views and a draggable ROI (FrameLabel)."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PyQt6.QtWidgets import QLabel, QMenu, QSizePolicy

from config import MIN_ROI_SIDE
from structures import DisplayFrame, ViewMode


class FrameLabel(QLabel):
    """
    Presents the latest {original, processed} pair and lets the user drag a ROI.

    Rendering goes through ``paintEvent``/QPainter rather than ``setPixmap``
    because the comparison views need two independently letterboxed viewports in
    one widget, which a single pixmap cannot express. QPainter is also what the
    rest of this port uses; the C++ original's OpenGL path is deliberately not
    reproduced.
    """

    roi_changed = pyqtSignal(QRect)

    #: Fraction of pane width used for the label text height.
    _LABEL_FRACTION = 0.045

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._frame: DisplayFrame | None = None
        self._view_mode = ViewMode.PROCESSED
        self._origin = QPoint()
        self._dragging = False
        self._rubber = QRect()
        self._placeholder = ""
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._menu = QMenu(self)
        act_clear = self._menu.addAction("Reset ROI to full frame")
        act_clear.triggered.connect(self._clear_roi)

    # ------------------------------------------------------------------ input

    def _show_context_menu(self, pos: QPoint) -> None:
        self._menu.exec(self.mapToGlobal(pos))

    def _clear_roi(self) -> None:
        """Full ROI = the whole captured frame, not the size of the QImage shown."""
        self._rubber = QRect()
        self._dragging = False
        self.update()
        self.roi_changed.emit(QRect(0, 0, 0, 0))

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        """Keep the "waiting for video" placeholder working under custom painting."""
        self._placeholder = text
        super().setText("")
        self.update()

    def set_view_mode(self, mode: ViewMode) -> None:
        self._view_mode = mode
        self.update()

    def view_mode(self) -> ViewMode:
        return self._view_mode

    def set_display_frame(self, frame: DisplayFrame) -> None:
        """
        Accept the pair as one object.

        Taking a single DisplayFrame (instead of two setters) is what guarantees
        the comparison panes are in lockstep: there is no intermediate state in
        which one pane has been updated and the other has not.
        """
        self._frame = frame
        self._placeholder = ""
        self.update()

    # ------------------------------------------------------------------ layout

    def _pane_regions(self) -> list[tuple[QRectF, bool]]:
        """
        Widget-space viewports as (region, wants_original) pairs.

        Mirrors the pane table of the C++ DisplayWidget: original always takes
        the first pane (left / top) so the eye reads before-then-after.
        """
        w = float(self.width())
        h = float(self.height())
        if self._view_mode is ViewMode.PROCESSED:
            return [(QRectF(0, 0, w, h), False)]
        if self._view_mode is ViewMode.ORIGINAL:
            return [(QRectF(0, 0, w, h), True)]
        if self._view_mode is ViewMode.SIDE_BY_SIDE:
            half = w * 0.5
            return [(QRectF(0, 0, half, h), True), (QRectF(half, 0, w - half, h), False)]
        half = h * 0.5
        return [(QRectF(0, 0, w, half), True), (QRectF(0, half, w, h - half), False)]

    def _image_for(self, wants_original: bool) -> QImage | None:
        """Fall back to the processed image when the original tap is absent."""
        if self._frame is None:
            return None
        if wants_original:
            return self._frame.original or self._frame.processed
        return self._frame.processed

    @staticmethod
    def _letterbox(region: QRectF, img: QImage) -> QRectF:
        """Largest rect inside `region` with the image's aspect ratio, centred."""
        if img.width() <= 0 or img.height() <= 0:
            return region
        aspect = img.width() / img.height()
        cw = region.width()
        ch = cw / aspect
        if ch > region.height():
            ch = region.height()
            cw = ch * aspect
        return QRectF(
            region.x() + (region.width() - cw) * 0.5,
            region.y() + (region.height() - ch) * 0.5,
            cw,
            ch,
        )

    def _primary_pane(self) -> tuple[QRectF, QImage] | None:
        """
        Pane the ROI is measured in: the one showing the *processed* stream.

        In comparison views the two panes show the same geometry, so which one
        is picked only decides where the rubber band may be drawn; using the
        processed pane keeps single-view behaviour identical to before.
        """
        panes = self._pane_regions()
        for region, wants_original in panes:
            if not wants_original:
                img = self._image_for(False)
                if img is not None and not img.isNull():
                    return self._letterbox(region, img), img
        for region, wants_original in panes:
            img = self._image_for(wants_original)
            if img is not None and not img.isNull():
                return self._letterbox(region, img), img
        return None

    def _pixmap_rect(self) -> QRect | None:
        """Integer bounds of the primary pane's image, for hit-testing."""
        pane = self._primary_pane()
        return pane[0].toRect() if pane is not None else None

    # ------------------------------------------------------------------ paint

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(18, 18, 20))
        if self._frame is None:
            if self._placeholder:
                p.setPen(QColor(200, 200, 200))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
            return

        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        panes = self._pane_regions()
        multi = len(panes) > 1
        for region, wants_original in panes:
            img = self._image_for(wants_original)
            if img is None or img.isNull():
                continue
            target = self._letterbox(region, img)
            p.drawImage(target, img)
            # Label the panes whenever there is something to disambiguate; the
            # single views are labelled too so a screenshot is self-describing.
            self._draw_label(
                p, target, "Original" if wants_original else "Processed", multi
            )

        if multi:
            # Hairline between the panes: without it two similar frames read as
            # one wide image.
            p.setPen(QPen(QColor(70, 70, 76), 1))
            if self._view_mode is ViewMode.SIDE_BY_SIDE:
                x = int(self.width() * 0.5)
                p.drawLine(x, 0, x, self.height())
            else:
                y = int(self.height() * 0.5)
                p.drawLine(0, y, self.width(), y)

        if not self._rubber.isNull():
            p.setPen(QPen(Qt.GlobalColor.green, 1))
            p.drawRect(self._rubber)

    def _draw_label(
        self, p: QPainter, target: QRectF, text: str, emphasise: bool
    ) -> None:
        """Overlay the pane name on a translucent plate so it reads on any frame."""
        px = max(9, int(target.width() * self._LABEL_FRACTION))
        font = QFont(p.font())
        font.setPixelSize(px)
        font.setBold(emphasise)
        p.setFont(font)
        metrics = p.fontMetrics()
        pad = max(3, px // 3)
        tw = metrics.horizontalAdvance(text)
        th = metrics.height()
        plate = QRectF(
            target.x() + pad, target.y() + pad, tw + 2 * pad, th + 2 * pad * 0.5
        )
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(plate, 3, 3)
        p.setPen(QColor(255, 255, 255))
        p.drawText(plate, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()

    # ------------------------------------------------------------------ mouse

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._frame is not None:
            self._dragging = True
            self._origin = event.position().toPoint()
            self._rubber = QRect(self._origin, self._origin)
            pr = self._pixmap_rect()
            if pr is not None:
                self._rubber = self._rubber.normalized().intersected(pr)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and self._frame is not None:
            raw = QRect(self._origin, event.position().toPoint()).normalized()
            pr = self._pixmap_rect()
            self._rubber = raw.intersected(pr) if pr is not None else raw
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
        pane = self._primary_pane()
        if pane is None:
            return
        pr_f, img = pane
        pr = pr_f.toRect()
        rubber = self._rubber.normalized().intersected(pr)
        if rubber.width() < 4 or rubber.height() < 4:
            self.roi_changed.emit(QRect(0, 0, 0, 0))
            return
        sx = img.width() / max(1, pr.width())
        sy = img.height() / max(1, pr.height())
        ix = max(0, int(round((rubber.x() - pr.x()) * sx)))
        iy = max(0, int(round((rubber.y() - pr.y()) * sy)))
        iw = min(img.width() - ix, int(round(rubber.width() * sx)))
        ih = min(img.height() - iy, int(round(rubber.height() * sy)))
        if iw < MIN_ROI_SIDE or ih < MIN_ROI_SIDE:
            self.roi_changed.emit(QRect(0, 0, 0, 0))
            return
        self.roi_changed.emit(QRect(ix, iy, iw, ih))
