"""Keep ordinary windows independent from the floating always-on-top control."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog


class DesktopDialog(QDialog):
    def __init__(self, parent=None):
        # Windows keeps owned windows above their owner, even without TOPMOST.
        # Never give an ordinary dialog the overlay as a native/transient owner.
        if parent is not None and parent.window().windowFlags() & Qt.WindowType.WindowStaysOnTopHint:
            parent = None
        super().__init__(parent)
        if parent is None:
            self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowTitleHint
                                | Qt.WindowType.WindowSystemMenuHint
                                | Qt.WindowType.WindowMinMaxButtonsHint
                                | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        if parent is not None:
            self.setWindowModality(Qt.WindowModality.WindowModal)


def present_window(window):
    if window.isMinimized():
        window.showNormal()
    else:
        window.show()
    window.raise_()
    window.activateWindow()
