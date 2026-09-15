import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF
from PySide6.QtGui import QEnterEvent, QFontDatabase
from PySide6.QtWidgets import QApplication

from local_meeting_assistant.appearance import STYLES, AnimatedButton, get_style, ICON_PRESETS, ICON_COLOR_LABELS
from local_meeting_assistant.config import AppConfig
from local_meeting_assistant.ui import AppearanceDialog, OverlayWindow, PreviewController


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    for font in ("segoeui.ttf", "consola.ttf"):
        QFontDatabase.addApplicationFont(f"C:/Windows/Fonts/{font}")
    return instance


@pytest.mark.parametrize("style", STYLES)
def test_switch_preserves_recording_and_controls_fit(app, style):
    controller = PreviewController()
    overlay = OverlayWindow(controller, AppConfig(), preview=True)
    overlay.show()
    overlay._set_expanded(True)
    controller.recording_changed.emit(True, "teams")
    controller.track_changed.emit("mic", True)
    overlay.apply_style(style)
    controller.message.emit("Messaggio molto lungo " * 60)
    app.processEvents()
    assert overlay._recording and overlay._mic_active
    assert overlay.stop_button.isEnabled() and not overlay.record_button.isEnabled()
    assert overlay.width() == STYLES[style].width
    for button in (
        overlay.record_button,
        overlay.stop_button,
        overlay.cancel_button,
        overlay.archive_button,
        overlay.appearance_button,
    ):
        origin = button.mapTo(overlay.panel, QPoint(0, 0))
        assert overlay.panel.rect().contains(origin)
        assert overlay.panel.rect().contains(
            origin + QPoint(button.width() - 1, button.height() - 1)
        )
    overlay._set_expanded(False)
    assert overlay.size().toTuple() == (88, 88)
    overlay.close()


def test_preview_does_not_apply_until_saved_and_survives_restart(app, tmp_path):
    config = AppConfig()
    overlay = OverlayWindow(PreviewController(), config, preview=True)
    dialog = AppearanceDialog(overlay)
    dialog.styles_list.setCurrentRow(3)
    dialog._set_icon_color("body", "#112233")
    assert config.icon_colors == {}
    dialog.motion_check.setChecked(True)
    assert overlay.style.key == "obsidian"
    assert config.appearance_style == "obsidian"
    path = tmp_path / "config.json"
    original_save = AppConfig.save
    with patch.object(AppConfig, "save", lambda self: original_save(self, path)):
        dialog._apply()
    restored = AppConfig.load(path)
    assert restored.appearance_style == "cyberpunk"
    assert restored.reduce_motion
    assert restored.icon_colors["cyberpunk"]["body"] == "#112233"
    assert overlay.style.key == "cyberpunk"
    dialog.close()
    overlay.close()


def test_icon_color_drafts_are_isolated_per_style_and_resettable(app):
    config = AppConfig(icon_colors={"cyberpunk": {"body": "#112233"}})
    overlay = OverlayWindow(PreviewController(), config, preview=True)
    dialog = AppearanceDialog(overlay)
    dialog.styles_list.setCurrentRow(3)
    dialog._set_icon_color("body", "#445566")
    dialog.styles_list.setCurrentRow(1)
    dialog._set_icon_color("ink", "#abcdef")
    assert config.icon_colors == {"cyberpunk": {"body": "#112233"}}
    dialog.styles_list.setCurrentRow(3)
    assert dialog.preview_config.icon_colors["cyberpunk"]["body"] == "#445566"
    assert dialog.preview.record_button.style.accent == STYLES["cyberpunk"].accent
    dialog._reset_colors()
    assert "cyberpunk" not in dialog.preview_config.icon_colors
    assert dialog.preview_config.icon_colors["aurora"]["ink"] == "#abcdef"
    dialog.sync_selection()
    assert dialog.preview_config.icon_colors == config.icon_colors
    dialog.close()
    overlay.close()


def test_button_glitch_only_runs_on_enabled_cyber_hover(app):
    button = AnimatedButton("Archivio")
    button.set_style(get_style("cyberpunk"))
    button.show()
    assert not button.glitch_active
    enter = lambda: QEnterEvent(QPointF(3, 3), QPointF(3, 3), QPointF(3, 3))
    app.sendEvent(button, enter())
    assert button.glitch_active and button._glitch_timer.isActive()
    button._glitch_tick()
    assert button._glitch_elapsed > 0
    app.sendEvent(button, QEvent(QEvent.Type.Leave))
    assert not button.glitch_active and not button._glitch_timer.isActive()
    app.sendEvent(button, enter())
    button.setEnabled(False)
    assert not button.glitch_active and not button._glitch_timer.isActive()
    button.setEnabled(True)
    button.set_style(get_style("cyberpunk"), reduce_motion=True)
    assert not button.glitch_active
    button.set_style(get_style("aurora"))
    assert not button.glitch_active
    button.close()


def test_audio_bars_settle_after_input_stops(app):
    controller = PreviewController()
    overlay = OverlayWindow(controller, AppConfig(), preview=True)
    overlay.show()
    controller.level_changed.emit("system", 0.8)
    overlay._animate()
    assert overlay._display_level > 0.1
    overlay._levels = {"mic": (0, 0), "system": (0.8, 0)}
    for _ in range(80):
        overlay._animate()
    assert overlay._display_level < 0.0001
    assert max(overlay.mic_meter.history) < 0.0001
    overlay.close()


@pytest.mark.parametrize("style", STYLES)
def test_custom_palette_updates_entire_panel_not_only_icon(app, style):
    colors = dict(zip(ICON_COLOR_LABELS, ICON_PRESETS["Ambra"]))
    overlay = OverlayWindow(PreviewController(), AppConfig(
        appearance_style=style, icon_colors={style: colors}), preview=True)
    overlay.show()
    overlay._set_expanded(True)
    app.processEvents()
    assert overlay.panel.style.background == colors["body"]
    assert overlay.panel.style.surface == colors["hover"]
    assert overlay.panel.style.accent == colors["edge"]
    for button in (overlay.record_button, overlay.stop_button, overlay.archive_button,
                   overlay.cancel_button, overlay.appearance_button):
        assert button.style == overlay.panel.style
        assert button.style.key == style
    assert STYLES[style].background != colors["body"]  # base theme remains intact
    overlay.close()


def test_palette_preview_and_apply_update_panel_without_changing_recording(app, tmp_path):
    config = AppConfig()
    controller = PreviewController()
    overlay = OverlayWindow(controller, config, preview=True)
    controller.recording_changed.emit(True, "teams")
    dialog = AppearanceDialog(overlay)
    dialog.styles_list.setCurrentRow(3)
    dialog._preset_selected(list(ICON_PRESETS).index("Salvia") + 1)
    assert dialog.preview.panel.style.background == "#20382f"
    assert overlay.panel.style.background == STYLES["obsidian"].background
    original_save = AppConfig.save
    with patch.object(AppConfig, "save", lambda self: original_save(self, tmp_path / "config.json")):
        dialog._apply()
    assert overlay.panel.style.background == "#20382f"
    assert overlay.archive_button.style.accent == "#9dcea7"
    assert overlay._recording and overlay.stop_button.isEnabled()
    assert not overlay.record_button.isEnabled()
    dialog._reset_colors()
    assert dialog.preview.panel.style == STYLES["cyberpunk"]
    dialog.close()
    overlay.close()
