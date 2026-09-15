import threading
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from local_meeting_assistant.config import AppConfig
from local_meeting_assistant.controller import AssistantController
from local_meeting_assistant.domain import SessionSource, SessionStatus
from local_meeting_assistant.storage import SessionRepository
from local_meeting_assistant.ui import RecapDialog, ArchiveDialog


def session(tmp_path):
    repo = SessionRepository(tmp_path / "archive")
    record = repo.create(SessionSource.MANUAL, "Meeting fittizio")
    record.status = SessionStatus.COMPLETE
    repo.save_record(record)
    return repo, record


def controller(repo):
    # No capture, model loading, Teams probing or real archive access in these tests.
    result = AssistantController.__new__(AssistantController)
    QObject.__init__(result)
    result._lock = threading.RLock()
    result._current = None
    result._processing = set()
    result.repository = repo
    return result


def test_model_selection_and_backspacing_cannot_grow_window(qt_application_lifetime):
    dialog = RecapDialog(AppConfig())
    for i in range(5):
        key = f"demo-{i}"
        dialog._catalog[key] = {"key": key, "description": "Nota fittizia lunga. " * (i * 60)}
        dialog.model.addItem(key)
    dialog.show()
    qt_application_lifetime.processEvents()
    initial = dialog.size()
    for i in range(60):
        dialog.model.setCurrentText(f"demo-{i % 5}")
        qt_application_lifetime.processEvents()
        assert dialog.size() == initial
        assert not hasattr(dialog, "model_info")
    dialog.model.setCurrentText("demo-4")
    assert "Nota fittizia" in dialog.model.toolTip()
    # Reproduce deleting the model name one character at a time, repeatedly.
    for _ in range(5):
        dialog.model.setEditText("modello-con-un-nome-molto-lungo" * 10)
        while dialog.model.currentText():
            dialog.model.lineEdit().backspace()
            qt_application_lifetime.processEvents()
            assert dialog.size() == initial
    with patch("local_meeting_assistant.ui.QToolTip.showText") as tooltip:
        dialog.model.setCurrentText("demo-4")
        dialog.model.activated.emit(dialog.model.currentIndex())
        tooltip.assert_called_once()
        assert "Nota fittizia" in tooltip.call_args.args[1]
        tooltip.reset_mock()
        dialog.model_info_button.click()
        tooltip.assert_called_once()
    # Exercise the native signature too, not only the mocked signal wiring.
    dialog.show_model_tooltip()
    dialog.resize(500, 380)
    qt_application_lifetime.processEvents()
    assert dialog.height() == 380
    assert dialog.form_scroll.verticalScrollBar().maximum() > 0
    for button in dialog.findChildren(QPushButton):
        if button.text() in ("Annulla", "Genera recap"):
            assert dialog.rect().contains(button.geometry())
    dialog.close()


def test_rename_changes_metadata_only_and_survives_reload(tmp_path):
    repo, record = session(tmp_path)
    source = record.directory / "transcript.md"
    source.write_text("Testo completamente fittizio", encoding="utf-8")
    original_dir = record.directory
    repo.rename(record, "  Riunione   progetto demo  ")
    assert repo.list_records()[0].title == "Riunione progetto demo"
    assert record.title == "Riunione progetto demo" and record.directory == original_dir
    assert source.read_text(encoding="utf-8") == "Testo completamente fittizio"


@pytest.mark.parametrize("title", ["", "   ", "x" * 201])
def test_invalid_rename_preserves_title(tmp_path, title):
    repo, record = session(tmp_path)
    with pytest.raises(ValueError):
        repo.rename(record, title)
    assert repo.list_records()[0].title == "Meeting fittizio"


def test_trash_targets_only_session_and_does_not_fallback_to_delete(tmp_path):
    repo, record = session(tmp_path)
    with patch("send2trash.send2trash") as trash:
        repo.trash(record)
        trash.assert_called_once_with(str(record.directory.resolve()))
    with patch("send2trash.send2trash", side_effect=OSError("Cestino non disponibile")):
        with pytest.raises(OSError):
            repo.trash(record)
    assert record.directory.is_dir() and (record.directory / "session.json").is_file()


def test_trash_rejects_root_outside_and_wrong_identity(tmp_path):
    repo, record = session(tmp_path)
    original = record.directory
    with patch("send2trash.send2trash") as trash:
        for path in (repo.root, tmp_path):
            record.directory = path
            with pytest.raises(ValueError):
                repo.trash(record)
        record.directory = original
        record.session_id = "wrong-id"
        with pytest.raises(ValueError):
            repo.trash(record)
        trash.assert_not_called()


def test_stale_ui_cannot_delete_a_now_busy_record(tmp_path):
    repo, record = session(tmp_path)
    fresh = repo.load_record(record.directory / "session.json")
    fresh.status = SessionStatus.SUMMARIZING
    repo.save_record(fresh)
    with patch("send2trash.send2trash") as trash:
        with pytest.raises(ValueError):
            repo.trash(record)
        with pytest.raises(ValueError):
            repo.rename(record, "Nuovo titolo")
        trash.assert_not_called()


def test_controller_blocks_queued_jobs_and_current_recording(tmp_path):
    repo, record = session(tmp_path)
    ctrl = controller(repo)
    with patch.object(repo, "trash") as trash, patch.object(repo, "rename") as rename:
        ctrl._processing.add(record.session_id)
        assert not ctrl.delete_session(record) and not ctrl.rename_session(record, "Nome")
        ctrl._processing.clear()
        ctrl._current = record
        assert not ctrl.delete_session(record) and not ctrl.rename_session(record, "Nome")
        trash.assert_not_called()
        rename.assert_not_called()


def test_archive_rename_and_delete_confirmation(tmp_path):
    repo, record = session(tmp_path)
    ctrl = controller(repo)
    dialog = ArchiveDialog(ctrl)
    dialog.show()
    QApplication.instance().processEvents()
    assert dialog.rename_button.isVisible() and dialog.delete_button.isVisible()
    assert dialog.rect().contains(dialog.rename_button.geometry())
    assert dialog.rect().contains(dialog.delete_button.geometry())
    dialog.grab().save(str(tmp_path / "archive-demo.png"))
    with patch("local_meeting_assistant.ui.QInputDialog.getText", return_value=("Demo rinominata", True)):
        dialog._rename_selected()
    assert dialog.table.item(0, 1).text() == "Demo rinominata"
    with patch.object(ctrl, "delete_session", return_value=True) as delete:
        def decline(box):
            assert box.defaultButton() == box.button(QMessageBox.StandardButton.No)
            return QMessageBox.StandardButton.No
        with patch.object(QMessageBox, "exec", decline):
            dialog._delete_selected()
        delete.assert_not_called()
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Yes):
            dialog._delete_selected()
        delete.assert_called_once()
    dialog.close()
