"""Main window with one tab per source (MainWindow)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
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


class MainWindow(QMainWindow):
    """Tab container: one tab per open camera or video."""

    def __init__(self) -> None:
        super().__init__()
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

        act_about = QAction("About", self)
        act_about.triggered.connect(self._about)
        self.menuBar().addAction(act_about)

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
            title = f"Camera {dlg.device_id()}"
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

    def closeEvent(self, event) -> None:
        for i in range(self._tabs.count()):
            w = self._tabs.widget(i)
            if isinstance(w, CameraTab):
                w.shutdown()
        super().closeEvent(event)


def run_app() -> None:
    import sys

    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
