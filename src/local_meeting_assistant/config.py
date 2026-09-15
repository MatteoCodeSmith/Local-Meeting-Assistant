from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def _app_config_dir() -> Path:
    try:
        from platformdirs import user_config_path

        return Path(user_config_path("LocalMeetingAssistant", appauthor=False))
    except ImportError:
        return Path(os.environ.get("APPDATA", Path.home())) / "LocalMeetingAssistant"


def _default_recordings_dir() -> Path:
    return Path.home() / "Documents" / "Local Meeting Assistant" / "recordings"


@dataclass(slots=True)
class AppConfig:
    recordings_dir: str = str(_default_recordings_dir())
    auto_detect_teams: bool = True
    auto_record_teams: bool = True
    teams_poll_seconds: float = 2.0
    teams_start_confirmations: int = 3
    teams_end_confirmations: int = 15
    teams_speaker_names: bool = True
    local_display_name: str = ""
    sample_rate: int = 48_000
    capture_block_seconds: float = 0.25
    transcription_chunk_seconds: int = 30
    capture_microphone: bool = True
    capture_system_audio: bool = True
    follow_default_microphone: bool = True
    audio_device_poll_seconds: float = 1.0
    microphone_name: str = ""
    speaker_name: str = ""
    live_transcription: bool = True
    live_voice_recognition: bool = True
    transcription_backend: str = "lmstudio_gguf"
    whisper_gguf_path: str = ""
    transcribe_device_preference: str = "nvidia"
    preload_whisper: bool = True
    whisper_model: str = "small"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "int8_float16"
    transcription_language: str = "auto"
    lm_base_url: str = "http://127.0.0.1:1234/v1"
    lm_model: str = "google/gemma-3-4b"
    lm_timeout_seconds: int = 180
    lm_context_length: int = 16_384
    lm_gpu_kv_cache: bool = False
    summary_chunk_characters: int = 12_000
    recap_language: str = "Italiano"
    delete_audio_after_summary: bool = False
    overlay_x: int | None = None
    overlay_y: int | None = None
    appearance_style: str = "obsidian"
    reduce_motion: bool = False
    icon_colors: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        config_path = path or (_app_config_dir() / "config.json")
        if not config_path.exists():
            config = cls()
            config.save(config_path)
            return config
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in raw.items() if key in allowed})

    def save(self, path: Path | None = None) -> Path:
        config_path = path or (_app_config_dir() / "config.json")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = config_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(config_path)
        return config_path

    @property
    def recordings_path(self) -> Path:
        return Path(self.recordings_dir).expanduser()
