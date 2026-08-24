"""Main window with one tab per source (MainWindow)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QActionGroup, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
)

from ui.camera_tab import CameraTab
from ui.connect_dialog import ConnectDialog
from ui.theme import ColorScheme, ThemeManager


class MainWindow(QMainWindow):
    """Tab container: one tab per open camera or video."""

    def __init__(self, theme: ThemeManager | None = None) -> None:
        super().__init__()
        self._theme = theme
        self.setWindowTitle("Live Video Magnification (Python/PyQt)")
        self.resize(1200, 720)

        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        self.setCentralWidget(self._tabs)

        placeholder = QLabel("Connect a camera or open a video (Ctrl+O).")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tabs.addTab(placeholder, "Start")

        act_open = QAction("Connect / Open…", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self._connect)
        self.menuBar().addAction(act_open)

        act_quit = QAction("Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        self.menuBar().addAction(act_quit)

        self._build_view_menu()

        act_about = QAction("About", self)
        act_about.triggered.connect(self._about)
        self.menuBar().addAction(act_about)

    def _build_view_menu(self) -> None:
        """Appearance and fullscreen live together under View."""
        view = self.menuBar().addMenu("View")

        act_full = QAction("Fullscreen", self)
        act_full.setShortcut(QKeySequence("F11"))
        act_full.setCheckable(True)
        act_full.triggered.connect(lambda on: self._set_fullscreen(on))
        view.addAction(act_full)
        self._act_fullscreen = act_full

        view.addSeparator()

        # Exclusive group: the three appearance options are one choice, and
        # "System" is the state where we keep tracking the OS.
        appearance = view.addMenu("Appearance")
        group = QActionGroup(self)
        group.setExclusive(True)
        self._appearance_actions: dict[str, QAction] = {}
        for key, label in (
            ("system", "Follow system"),
            ("light", "Light"),
            ("dark", "Dark"),
        ):
            act = QAction(label, self)
            act.setCheckable(True)
            act.triggered.connect(
                lambda _checked, k=key: self._set_appearance(k)
            )
            group.addAction(act)
            appearance.addAction(act)
            self._appearance_actions[key] = act

        act_toggle = QAction("Toggle light / dark", self)
        act_toggle.setShortcut(QKeySequence("Ctrl+Shift+L"))
        act_toggle.triggered.connect(self._toggle_appearance)
        view.addAction(act_toggle)

        self._sync_appearance_actions()

    def _set_appearance(self, key: str) -> None:
        if self._theme is None:
            return
        if key == "system":
            self._theme.apply_system()
        else:
            self._theme.override_scheme(
                ColorScheme.LIGHT if key == "light" else ColorScheme.DARK
            )
        self._sync_appearance_actions()

    def _toggle_appearance(self) -> None:
        if self._theme is None:
            return
        self._theme.toggle()
        self._sync_appearance_actions()

    def _sync_appearance_actions(self) -> None:
        """Reflect the manager's state; it, not the menu, is the source of truth."""
        if self._theme is None:
            return
        if self._theme.following_system:
            self._appearance_actions["system"].setChecked(True)
        elif self._theme.applied_scheme is ColorScheme.LIGHT:
            self._appearance_actions["light"].setChecked(True)
        else:
            self._appearance_actions["dark"].setChecked(True)

    def _set_fullscreen(self, on: bool) -> None:
        if on:
            self.showFullScreen()
        else:
            self.showNormal()
        self._act_fullscreen.setChecked(self.isFullScreen())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        F11 toggles fullscreen; Escape only acts while already fullscreen.

        Handled here rather than as an always-on QShortcut so that Escape keeps
        propagating normally to dialogs, spin boxes and combo popups when the
        window is not fullscreen.
        """
        key = event.key()
        if key == Qt.Key.Key_F11:
            self._set_fullscreen(not self.isFullScreen())
            return
        if key == Qt.Key.Key_Escape and self.isFullScreen():
            self._set_fullscreen(False)
            return
        super().keyPressEvent(event)

    def _connect(self) -> None:
        dlg = ConnectDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.is_camera():
            tab = CameraTab(
                True,
                device_id=dlg.device_id(),
                width=dlg.width(),
                height=dlg.height(),
                fps=dlg.fps(),
                drop=dlg.drop_frames(),
                queue_size=dlg.queue_size(),
            )
            # Prefer the driver-reported name: "Integrated Camera" tells the
            # user which tab is which, "Camera 2" does not.
            title = dlg.device_name() or f"Camera {dlg.device_id()}"
        else:
            path = dlg.video_path()
            if not path:
                QMessageBox.warning(self, "Error", "Please select a video file.")
                return
            tab = CameraTab(
                False,
                path=path,
                width=dlg.width(),
                height=dlg.height(),
                fps=dlg.fps(),
                drop=dlg.drop_frames(),
                queue_size=dlg.queue_size(),
            )
            title = path.split("/")[-1]

        if self._tabs.count() == 1 and isinstance(self._tabs.widget(0), QLabel):
            self._tabs.removeTab(0)
            self._tabs.setTabsClosable(True)

        self._tabs.addTab(tab, title)
        self._tabs.setCurrentWidget(tab)

    def _close_tab(self, index: int) -> None:
        w = self._tabs.widget(index)
        if isinstance(w, CameraTab):
            w.shutdown()
        self._tabs.removeTab(index)
        if self._tabs.count() == 0:
            ph = QLabel("Connect a camera or open a video (Ctrl+O).")
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._tabs.addTab(ph, "Start")

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About",
            "<p>Real-time Eulerian video magnification &mdash; a Python port "
            "built with PyQt6 and OpenCV.</p>"
            "<p>Based on the GPLv3 project "
            "<a href='https://github.com/tschnz/Live-Video-Magnification'>"
            "Live-Video-Magnification</a> by Jens Schindel.</p>",
        )

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """
        F11 toggles fullscreen; Escape leaves it.

        Handled here rather than as always-live QShortcuts so that Escape only
        acts when already fullscreen -- otherwise it would swallow the key from
        dialogs and from any widget that uses it to cancel.
        """
        key = event.key()
        if key == Qt.Key.Key_F11:
            self.setWindowState(
                self.windowState() ^ Qt.WindowState.WindowFullScreen
            )
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self.isFullScreen():
            self.setWindowState(
                self.windowState() & ~Qt.WindowState.WindowFullScreen
            )
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        for i in range(self._tabs.count()):
            w = self._tabs.widget(i)
            if isinstance(w, CameraTab):
                w.shutdown()
        super().closeEvent(event)


def run_app() -> None:
    import sys

    app = QApplication(sys.argv)
    theme = ThemeManager(app)
    # Start from the OS appearance; the View menu can pin it afterwards.
    theme.apply_system()
    win = MainWindow(theme)
    win.show()
    sys.exit(app.exec())
