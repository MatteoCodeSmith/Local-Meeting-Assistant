from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication
import sys
import ctypes
import pytest

from local_meeting_assistant.config import AppConfig
from local_meeting_assistant.ui import ArchiveDialog, OverlayWindow, PreviewController, AppearanceDialog
from local_meeting_assistant.window_policy import DesktopDialog, present_window


class FakeArchiveController(PreviewController):
    session_finished = Signal(object, object)

    def list_sessions(self):
        return []


def test_overlay_is_movable_desktop_window_but_stays_on_top():
    overlay = OverlayWindow(PreviewController(), AppConfig())
    assert overlay.windowType() == Qt.WindowType.Window
    assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert overlay.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert overlay.parentWidget() is None
    assert overlay.panel.isHidden()
    overlay._set_expanded(True)
    assert not overlay.panel.isHidden()
    overlay.close()


def test_archive_and_settings_have_no_topmost_owner():
    controller = FakeArchiveController()
    overlay = OverlayWindow(controller, AppConfig())
    for dialog in (ArchiveDialog(controller, overlay), AppearanceDialog(overlay)):
        assert dialog.parentWidget() is None
        assert dialog.windowType() == Qt.WindowType.Window
        assert not dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        assert dialog.windowFlags() & Qt.WindowType.WindowMinimizeButtonHint
        assert dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
        dialog.show()
        assert dialog.windowHandle().transientParent() is None
        assert not dialog.isModal()
        dialog.close()
    overlay.close()


def test_nested_dialog_keeps_normal_owner_and_restore_works():
    root = DesktopDialog()
    child = DesktopDialog(root)
    assert child.parentWidget() is root
    assert not child.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    root.showMinimized()
    QApplication.processEvents()
    present_window(root)
    assert not root.isMinimized()
    child.close()
    root.close()


def test_windows_native_ownership():
    if sys.platform != "win32" or QApplication.platformName() != "windows":
        pytest.skip("Richiede una prova esplicita con Qt Windows, non offscreen")
    overlay = OverlayWindow(FakeArchiveController(), AppConfig())
    archive = ArchiveDialog(overlay.controller, overlay)
    user32 = ctypes.windll.user32
    user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.GetWindow.restype = ctypes.c_void_p
    try:
        overlay.show()
        archive.show()
        QApplication.processEvents()
        overlay_handle, archive_handle = int(overlay.winId()), int(archive.winId())
        assert user32.GetWindowLongW(overlay_handle, -20) & 0x8  # WS_EX_TOPMOST
        assert not user32.GetWindowLongW(overlay_handle, -20) & 0x80  # WS_EX_TOOLWINDOW
        assert not user32.GetWindowLongW(archive_handle, -20) & 0x8
        assert not user32.GetWindow(archive_handle, 4)  # GW_OWNER
    finally:
        archive.close()
        overlay.close()
