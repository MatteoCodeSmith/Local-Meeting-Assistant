from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from local_meeting_assistant.config import AppConfig
from local_meeting_assistant.transcription import LocalProcessingPipeline


def pipeline():
    p = object.__new__(LocalProcessingPipeline)
    p.config = AppConfig()
    p._model_session = Mock()
    p._model_session.run.return_value = SimpleNamespace(
        language="it",
        segments=[SimpleNamespace(t0_ms=100, t1_ms=900, text="Una frase di esempio.")],
    )
    return p


def test_silence_never_calls_whisper(tmp_path):
    path = tmp_path / "silence.flac"
    sf.write(path, np.zeros(48000), 48000)
    p = pipeline()
    assert p._run_gguf_transcription(path)[0] == []
    p._model_session.run.assert_not_called()


def test_vad_rejects_noise_without_calling_whisper(tmp_path):
    path = tmp_path / "noise.flac"
    sf.write(path, np.ones(48000) * 0.02, 48000)
    p = pipeline()
    with patch("faster_whisper.vad.get_speech_timestamps", return_value=[]):
        assert p._run_gguf_transcription(path)[0] == []
    p._model_session.run.assert_not_called()


def test_vad_preserves_original_timestamps_and_explicit_language(tmp_path):
    path = tmp_path / "speech.flac"
    sf.write(path, np.ones(48000 * 4) * 0.02, 48000)
    p = pipeline()
    p.config.transcription_language = "it"
    with patch(
        "faster_whisper.vad.get_speech_timestamps", return_value=[{"start": 32000, "end": 48000}]
    ):
        rows, _ = p._run_gguf_transcription(path)
    assert rows[0].start == 2.1 and rows[0].end == 2.9
    assert p._model_session.run.call_args.kwargs["language"] == "it"
    assert not p._model_session.run.call_args.kwargs["family"].condition_on_prev_tokens
