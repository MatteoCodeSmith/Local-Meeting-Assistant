"""Keep one Qt application alive across tests, including native-library/GC tests."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def qt_application_lifetime():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app
    app.processEvents()


@pytest.fixture(autouse=True)
def dispose_test_widgets(qt_application_lifetime):
    yield
    from PySide6.QtCore import QCoreApplication, QEvent
    # Closing a Qt widget only hides it. Dispose timers/previews before the next
    # test advances the event loop, instead of leaving Python/C++ cycles for GC.
    widgets = qt_application_lifetime.topLevelWidgets()
    for widget in widgets:
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_application_lifetime.processEvents()
