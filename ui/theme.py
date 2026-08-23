"""
Application theme: semantic colour tokens, an 8pt spacing scale and a QSS
template.

Why tokens rather than colours sprinkled through the widgets: every widget then
asks for a *role* ("surface", "danger") instead of a hex value, so a second
colour scheme is a new palette rather than an audit of every file.  The QSS is a
single template with ``@token`` placeholders substituted from the active
palette, which keeps the light and dark builds structurally identical -- they
cannot drift apart.

The app follows the OS appearance (``QStyleHints.colorScheme()``) until the user
pins a scheme from the View menu; nothing is persisted between runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication, QStyleFactory


class ColorScheme(Enum):
    """The two concrete appearances the palette can produce."""

    DARK = "dark"
    LIGHT = "light"


@dataclass(frozen=True)
class ThemePalette:
    """The app's design tokens; every colour in the UI comes from here."""

    bg: str  # window / canvas chrome ground
    surface: str  # toolbars / inspector panels
    surface2: str  # status strip, sunken rows
    raised: str  # default buttons
    line: str  # hairline borders / separators
    text: str
    dim: str  # secondary text / labels
    faint: str  # tertiary text / disabled
    field: str  # text-entry background
    accent: str
    accent2: str  # gradient partner -- gradients only, never flat chrome
    accent_ink: str  # text/icon colour on top of an accent fill
    ok: str
    danger: str


class Metrics:
    """Spacing scale (8pt grid) and corner radii, in px."""

    SPACE1 = 4
    SPACE2 = 8
    SPACE3 = 12
    SPACE4 = 16
    SPACE5 = 24
    RADIUS = 8
    RADIUS_SMALL = 6


_DARK = ThemePalette(
    bg="#15110D",  # warm espresso
    surface="#211A14",
    surface2="#29211A",
    raised="#2C241C",
    line="#382E25",
    text="#F3ECE3",
    dim="#A99A8B",
    faint="#6E6359",
    field="#0F0C09",
    accent="#F4A23C",  # ember amber
    accent2="#F0476E",  # rose (gradients only)
    accent_ink="#2A1505",
    ok="#8FCB8A",
    danger="#F2606B",
)

_LIGHT = ThemePalette(
    bg="#EEF0F2",  # cool porcelain
    surface="#FFFFFF",
    surface2="#F4F6F8",
    raised="#FFFFFF",
    line="#D8DCE0",
    text="#1E1B17",
    dim="#6B6A66",
    faint="#9DA0A6",
    field="#FFFFFF",
    accent="#B8521C",  # burnt terracotta
    accent2="#B01E5B",  # deep rose
    accent_ink="#FFFFFF",
    ok="#2E9E63",
    danger="#C8473E",
)

# Monospace stack for numeric readouts, so digits have equal advance widths and
# a changing frame rate does not make the whole strip jitter left and right.
MONO_FAMILIES = ("DejaVu Sans Mono", "Cascadia Code", "Consolas", "Menlo")

_QSS_TEMPLATE = """
QToolTip {
    background: @surface2; color: @text;
    border: 1px solid @line; border-radius: @radiusSmall; padding: 5px 8px;
}

/* --- Buttons --- */
QPushButton {
    background: @raised; color: @text;
    border: 1px solid @line; border-radius: @radiusSmall; padding: 7px 13px;
}
QPushButton:hover    { border-color: @accent; }
QPushButton:pressed  { background: @surface2; }
QPushButton:checked  { background: @accent; color: @accentInk; border-color: transparent; }
QPushButton:disabled { color: @faint; border-color: @line; background: @surface; }
/* Primary action: set the dynamic property `accent` = true on the widget. */
QPushButton[accent="true"]          { background: @accent; color: @accentInk; border: none; padding: 8px 14px; }
QPushButton[accent="true"]:disabled { background: @surface2; color: @faint; }

/* --- Fields --- */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background: @field; color: @text;
    border: 1px solid @line; border-radius: @radiusSmall; padding: 6px 9px;
    selection-background-color: @accent; selection-color: @accentInk;
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover { border-color: @accent; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border-color: @accent; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: @faint; }
QComboBox::drop-down { border: none; width: 20px; }
/* Our combo styling otherwise trips Qt's scrolling-popup mode, which clips
   short combos; listing up to maxVisibleItems avoids the scroller entirely. */
QComboBox { combobox-popup: 0; }
QComboBox QAbstractItemView {
    background: @surface2; color: @text;
    border: 1px solid @line; border-radius: @radiusSmall;
    selection-background-color: @accent; selection-color: @accentInk;
    outline: none; padding: 4px;
}
/* A styled combo's tight field padding otherwise collapses the popup's item
   heights so options overlap.  Unqualified on purpose: the popup view is not a
   QSS descendant of the combo by objectName. */
QComboBox QAbstractItemView::item { min-height: 24px; }

/* --- Check / radio --- */
QCheckBox, QRadioButton { color: @text; spacing: 8px; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; }
QCheckBox::indicator { border: 1px solid @line; border-radius: 4px; background: @field; }
QCheckBox::indicator:hover   { border-color: @accent; }
QCheckBox::indicator:checked { background: @accent; border-color: transparent; }
QCheckBox:disabled           { color: @faint; }

/* --- Group boxes (inspector sections) --- */
QGroupBox {
    border: 1px solid @line; border-radius: @radius;
    margin-top: 14px; padding: 10px 10px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 10px; padding: 0 4px;
    color: @dim; font-weight: 700;
}

/* --- Sliders --- */
QSlider::groove:horizontal   { height: 5px; border-radius: 3px; background: @line; }
QSlider::sub-page:horizontal { height: 5px; border-radius: 3px; background: @accent; }
QSlider::handle:horizontal {
    width: 15px; height: 15px; margin: -6px 0; border-radius: 8px;
    background: #FFFFFF; border: 1px solid @line;
}
QSlider::handle:horizontal:hover { border-color: @accent; }

/* --- Progress --- */
QProgressBar {
    background: @field; border: 1px solid @line; border-radius: @radiusSmall;
    text-align: center; color: @text; height: 16px;
}
QProgressBar::chunk { background: @accent; border-radius: 5px; }

/* --- Lists --- */
QListWidget { background: @field; border: 1px solid @line; border-radius: @radiusSmall; outline: none; }
QListWidget::item { padding: 7px 9px; border-radius: @radiusSmall; }
QListWidget::item:selected { background: @accent; color: @accentInk; }
QListWidget::item:hover:!selected { background: @surface2; }

/* --- Separators (HLine = shape 4, VLine = shape 5) --- */
QFrame[frameShape="4"] { background: @line; border: none; max-height: 1px; }
QFrame[frameShape="5"] { background: @line; border: none; max-width: 1px; }

/* --- Scrollbars --- */
QScrollBar:vertical   { background: transparent; width: 11px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 0; }
QScrollBar::handle:vertical   { background: @line; border-radius: 5px; min-height: 28px; }
QScrollBar::handle:horizontal { background: @line; border-radius: 5px; min-width: 28px; }
QScrollBar::handle:hover { background: @faint; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* --- Menus / tabs / dialogs --- */
QMenuBar { background: @surface; color: @text; border-bottom: 1px solid @line; }
QMenuBar::item { padding: 5px 10px; border-radius: @radiusSmall; }
QMenuBar::item:selected { background: @accent; color: @accentInk; }
QMenu { background: @surface2; color: @text; border: 1px solid @line; border-radius: @radius; padding: 5px; }
QMenu::item { padding: 6px 22px; border-radius: @radiusSmall; }
QMenu::item:selected { background: @accent; color: @accentInk; }
QDialog { background: @bg; }
QTabWidget::pane { border: 1px solid @line; border-radius: @radius; }
QTabBar::tab {
    background: @surface; color: @dim;
    border: 1px solid @line; border-bottom: none;
    border-top-left-radius: @radiusSmall; border-top-right-radius: @radiusSmall;
    padding: 6px 12px; margin-right: 2px;
}
QTabBar::tab:selected { background: @surface2; color: @text; }

/* --- Spin step buttons: slim and themed (arrows stay Fusion-drawn) --- */
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border; width: 16px; background: @surface2; border: none;
}
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: @radiusSmall; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: @radiusSmall; }

/* --- Status strip --- */
QWidget#statusStrip { background: @surface; border-top: 1px solid @line; }
QLabel#statCaption  { color: @dim; }
QLabel#statSlash    { color: @dim; }
/* A CSS-drawn circle whose fill is the health colour, set via `state`. */
QLabel#statDot {
    min-width: 8px; max-width: 8px; min-height: 8px; max-height: 8px;
    border-radius: 4px; background: @faint;
}
QLabel#statDot[state="ok"]   { background: @ok; }
QLabel#statDot[state="warn"] { background: @accent; }
QLabel#statDot[state="bad"]  { background: @danger; }
QLabel#statDot[state="idle"] { background: @faint; }
/* Calm @text when healthy, so colour only ever signals an exception. */
QLabel#statValue               { color: @text; }
QLabel#statValue[state="warn"] { color: @accent; }
QLabel#statValue[state="bad"]  { color: @danger; }
QLabel#statValue[state="idle"] { color: @faint; }
QLabel#statHint { color: @danger; padding-left: 2px; }
QDoubleSpinBox#statSpin { padding: 1px 6px; }
"""

# Longest-token-first, because a plain textual replace of "@surface" would also
# eat the prefix of "@surface2" and leave a stray "2" in the stylesheet.
_TOKEN_ORDER = (
    "bg",
    "surface2",
    "surface",
    "raised",
    "line",
    "text",
    "dim",
    "faint",
    "field",
    "accentInk",
    "accent2",
    "accent",
    "ok",
    "danger",
)

_TOKEN_ATTR = {
    "accentInk": "accent_ink",
}


def palette(scheme: ColorScheme) -> ThemePalette:
    """Return the token set for a scheme."""
    return _DARK if scheme is ColorScheme.DARK else _LIGHT


def style_sheet(p: ThemePalette) -> str:
    """Substitute the palette's tokens into the QSS template."""
    css = _QSS_TEMPLATE
    for token in _TOKEN_ORDER:
        value = getattr(p, _TOKEN_ATTR.get(token, token))
        css = css.replace("@" + token, value)
    # Radii after the colours, and again longest-first.
    css = css.replace("@radiusSmall", f"{Metrics.RADIUS_SMALL}px")
    css = css.replace("@radius", f"{Metrics.RADIUS}px")
    return css


def system_scheme() -> ColorScheme:
    """OS appearance via QStyleHints; falls back to dark when unknown."""
    app = QApplication.instance()
    hints = app.styleHints() if app is not None else None
    if hints is not None and hints.colorScheme() == Qt.ColorScheme.Light:
        return ColorScheme.LIGHT
    return ColorScheme.DARK


class ThemeManager:
    """
    Owns the session's appearance state and applies it to the QApplication.

    Kept as an object rather than module globals so a test can build one per
    QApplication without leaking state between cases.
    """

    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._following_system = True
        self._applied = system_scheme()
        # QStyleHints emits this when the desktop switches between light and
        # dark; while following the system we mirror it live.
        hints = app.styleHints()
        if hints is not None:
            hints.colorSchemeChanged.connect(self._on_system_scheme_changed)

    @property
    def applied_scheme(self) -> ColorScheme:
        return self._applied

    @property
    def following_system(self) -> bool:
        return self._following_system

    def apply(self, scheme: ColorScheme) -> None:
        """Set the Fusion style, the QPalette and the QSS for the whole app."""
        p = palette(scheme)
        dark = scheme is ColorScheme.DARK

        # Fusion is identical on every OS and fully honours QSS; the native
        # styles ignore parts of it, so light/dark would look inconsistent.
        # The string overload (rather than QStyleFactory.create) lets Qt own the
        # style object and keeps QStyle.name() reporting "fusion", which the
        # object form did not.
        if "Fusion" in QStyleFactory.keys():
            self._app.setStyle("Fusion")

        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor(p.bg))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(p.text))
        pal.setColor(QPalette.ColorRole.Base, QColor(p.field))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(p.surface2))
        pal.setColor(QPalette.ColorRole.Text, QColor(p.text))
        pal.setColor(QPalette.ColorRole.Button, QColor(p.raised))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(p.text))
        pal.setColor(QPalette.ColorRole.BrightText, QColor(p.danger))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(p.accent))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(p.accent_ink))
        pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.surface2))
        pal.setColor(QPalette.ColorRole.ToolTipText, QColor(p.text))
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(p.faint))
        pal.setColor(QPalette.ColorRole.Link, QColor(p.accent))
        pal.setColor(QPalette.ColorRole.Mid, QColor(p.line))
        pal.setColor(
            QPalette.ColorRole.Dark,
            QColor(p.bg).darker(120) if dark else QColor(p.line),
        )
        pal.setColor(
            QPalette.ColorRole.Shadow, QColor("#05070C" if dark else "#C2CAD6")
        )
        for role in (
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
        ):
            pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.faint))
        self._app.setPalette(pal)

        # OS-provided faces only: the nicer UI fonts are not redistributable.
        # Qt picks the first family that resolves on this machine.
        font = self._app.font()
        font.setFamilies(
            [
                "Inter",
                "SF Pro Text",
                "Segoe UI Variable Text",
                "Segoe UI",
                "Cantarell",
                "Noto Sans",
                "Ubuntu",
                "DejaVu Sans",
            ]
        )
        font.setPointSizeF(10.0)
        self._app.setFont(font)

        self._app.setStyleSheet(style_sheet(p))
        self._applied = scheme

    def apply_system(self) -> None:
        """Re-enable OS following and apply whatever the OS currently reports."""
        self._following_system = True
        self.apply(system_scheme())

    def override_scheme(self, scheme: ColorScheme) -> None:
        """Pin a scheme; the OS appearance is ignored from here on."""
        self._following_system = False
        self.apply(scheme)

    def toggle(self) -> None:
        """Flip between light and dark, pinning the result."""
        self.override_scheme(
            ColorScheme.LIGHT
            if self._applied is ColorScheme.DARK
            else ColorScheme.DARK
        )

    def _on_system_scheme_changed(self, _scheme: object = None) -> None:
        if self._following_system:
            self.apply(system_scheme())


def mono_font(base: QFont, *, point_size_factor: float = 1.0) -> QFont:
    """Return `base` restyled as a tabular monospace face."""
    f = QFont(base)
    f.setFamilies(list(MONO_FAMILIES))
    f.setStyleHint(QFont.StyleHint.Monospace)
    if point_size_factor != 1.0 and base.pointSizeF() > 0:
        f.setPointSizeF(base.pointSizeF() * point_size_factor)
    return f
