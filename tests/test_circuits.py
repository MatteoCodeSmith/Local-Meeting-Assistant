import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from local_meeting_assistant.appearance import get_style, icon_palette
from local_meeting_assistant.circuits import paint_circuits, pulse_progress, routes
from local_meeting_assistant.config import AppConfig
from local_meeting_assistant.ui import OverlayWindow, PreviewController


def test_pulses_move_inward_and_halo_is_static_when_idle_or_reduced():
    app = QApplication.instance() or QApplication([])
    center = QRectF(0, 0, 84, 84).center()
    for route in routes():
        assert route.point_at(0) == route.points[0]
        assert route.point_at(1) == route.points[-1]
        assert (route.points[-1] - center).manhattanLength() < (
            route.points[0] - center
        ).manhattanLength()
    assert pulse_progress(0.1, 0) < pulse_progress(0.8, 0) < pulse_progress(1.4, 0)

    def render(elapsed, **options):
        image = QPixmap(84, 84)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        paint_circuits(
            painter,
            QRectF(image.rect()),
            icon_palette(get_style("aurora")),
            elapsed=elapsed,
            **options,
        )
        painter.end()
        return image.toImage()

    assert render(0.1) == render(0.8)
    assert render(0.1, busy=True) != render(0.8, busy=True)
    assert render(0.1, busy=True, reduce_motion=True) == render(0.8, busy=True, reduce_motion=True)


def test_only_ai_status_starts_circuit_motion_and_status_updates_do_not_restart_it():
    app = QApplication.instance() or QApplication([])
    controller = PreviewController()
    overlay = OverlayWindow(controller, AppConfig(), preview=True)
    overlay.show()
    controller.recording_changed.emit(True, "manual")
    controller.level_changed.emit("mic", 0.7)
    overlay._animate()
    assert overlay._ai_phase == 0
    controller.ai_changed.emit(True, "Caricamento")
    overlay._animate()
    previous = overlay._ai_phase
    assert previous > 0
    controller.ai_changed.emit(True, "Recap")
    assert overlay._ai_phase == previous
    overlay.config.reduce_motion = True
    overlay._animate()
    assert overlay._ai_phase == previous
    controller.ai_changed.emit(False, "Pronta")
    assert overlay._ai_phase == 0
    overlay.close()
