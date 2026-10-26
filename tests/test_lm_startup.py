from unittest.mock import Mock

import pytest
from PySide6.QtCore import QProcess

from local_meeting_assistant.config import AppConfig
from local_meeting_assistant.lm_startup import LMStudioStartup, server_command


def fake_config(tmp_path, **kwargs):
    # Fake executable is never launched: all process calls below are mocked.
    path = tmp_path / "LM Studio tools" / "lms.exe"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"synthetic test fixture")
    return AppConfig(lms_executable=str(path), **kwargs)


def test_local_command_contains_no_model_or_prompt(tmp_path):
    config = fake_config(tmp_path, lm_base_url="http://localhost:2345/v1")
    executable, args = server_command(config)
    assert executable == config.lms_executable
    assert args == ["server", "start", "--port", "2345", "--bind", "127.0.0.1"]


@pytest.mark.parametrize("url", ["https://remote.example/v1", "http://192.168.1.2:1234/v1",
                                  "http://user:secret@localhost:1234/v1", "http://localhost:0/v1"])
def test_remote_or_invalid_endpoint_rejected(tmp_path, url):
    with pytest.raises(ValueError):
        server_command(fake_config(tmp_path, lm_base_url=url))


def test_missing_cli_gives_actionable_message(tmp_path):
    config = AppConfig(lms_executable=str(tmp_path / "missing.exe"))
    helper = LMStudioStartup(config)
    helper.start()
    assert "lms_executable" in helper.status
    assert helper.process.state() == QProcess.ProcessState.NotRunning


def test_start_nonblocking_no_duplicate_and_timeout(tmp_path):
    helper = LMStudioStartup(fake_config(tmp_path))
    process = Mock()
    helper.process = process
    process.state.return_value = QProcess.ProcessState.NotRunning
    helper.start()
    process.start.assert_called_once()
    assert helper.timer.isActive()
    process.state.return_value = QProcess.ProcessState.Running
    helper.start()
    process.start.assert_called_once()
    helper._timeout()
    process.kill.assert_called_once()
    assert "scaduto" in helper.status
    helper._finished(1, QProcess.ExitStatus.CrashExit)
    assert "scaduto" in helper.status
    assert not helper.timer.isActive()


def test_success_and_failure_are_reported(tmp_path):
    helper = LMStudioStartup(fake_config(tmp_path))
    helper._finished(0, QProcess.ExitStatus.NormalExit)
    assert "già attivo" in helper.status
    helper._finished(1, QProcess.ExitStatus.NormalExit)
    assert "fallito" in helper.status


def test_startup_preference_roundtrip(tmp_path):
    config = AppConfig(auto_start_lmstudio=False)
    path = tmp_path / "settings.json"
    config.save(path)
    assert not AppConfig.load(path).auto_start_lmstudio
