from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .domain import SessionRecord, SessionSource, SessionStatus, TranscriptSegment

LOGGER = logging.getLogger(__name__)


class SessionRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, source: SessionSource, title: str | None = None) -> SessionRecord:
        now = datetime.now(timezone.utc)
        session_id = uuid.uuid4().hex[:12]
        directory = self.root / f"{now.astimezone():%Y-%m-%d_%H-%M-%S}_{session_id}"
        directory.mkdir(parents=True, exist_ok=False)
        record = SessionRecord(
            session_id=session_id,
            directory=directory,
            source=source,
            title=title or ("Meeting Teams" if source is SessionSource.TEAMS else "Conversazione"),
            started_at=now,
        )
        self.save_record(record)
        return record

    def save_record(self, record: SessionRecord) -> None:
        self._write_json(record.directory / "session.json", record.to_dict())

    def save_transcript(
        self, record: SessionRecord, segments: list[TranscriptSegment], markdown: str
    ) -> tuple[Path, Path]:
        json_path = record.directory / "transcript.json"
        markdown_path = record.directory / "transcript.md"
        self._write_json(json_path, [segment.to_dict() for segment in segments])
        self._write_text(markdown_path, markdown)
        return json_path, markdown_path

    def save_recap(self, record: SessionRecord, recap: str, *, model_info: dict | None = None) -> Path:
        if model_info is not None:
            created = datetime.now(timezone.utc)
            run = record.directory / "recaps" / f"{created:%Y%m%d-%H%M%S-%f}_{uuid.uuid4().hex[:6]}"
            run.mkdir(parents=True, exist_ok=False)
            self._write_text(run / "recap.md", recap.rstrip() + "\n")
            self._write_json(run / "run.json", {**model_info, "created_at": created.isoformat()})
        path = record.directory / "recap.md"
        self._write_text(path, recap.rstrip() + "\n")
        return path

    @staticmethod
    def list_recaps(record: SessionRecord) -> list[dict]:
        """Read only run metadata, never the private recap text."""
        versions = []
        for meta in (record.directory / "recaps").glob("*/run.json"):
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
                if not isinstance(info, dict) or not (meta.parent / "recap.md").is_file():
                    continue
                versions.append({**info, "path": meta.parent / "recap.md"})
            except (OSError, ValueError):
                continue
        return sorted(versions, key=lambda info: str(info.get("created_at", "")), reverse=True)

    def list_records(self) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        for path in self.root.glob("*/session.json"):
            try:
                records.append(self.load_record(path))
            except Exception:
                LOGGER.warning("Metadati sessione non leggibili: %s", path, exc_info=True)
        return sorted(records, key=lambda item: item.started_at, reverse=True)

    @staticmethod
    def load_record(path: Path) -> SessionRecord:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return SessionRecord(
            session_id=str(raw["session_id"]),
            directory=Path(raw.get("directory") or path.parent),
            source=SessionSource(raw["source"]),
            status=SessionStatus(raw["status"]),
            started_at=datetime.fromisoformat(raw["started_at"]),
            ended_at=(datetime.fromisoformat(raw["ended_at"]) if raw.get("ended_at") else None),
            title=str(raw.get("title") or "Conversazione"),
            error=raw.get("error"),
        )

    def cancel(self, record: SessionRecord) -> None:
        record.status = SessionStatus.CANCELLED
        record.ended_at = datetime.now(timezone.utc)
        self.save_record(record)
        try:
            from send2trash import send2trash

            send2trash(str(record.directory))
        except (ImportError, OSError):
            # Cancellation is an explicit destructive action initiated by the user.
            shutil.rmtree(record.directory, ignore_errors=False)

    def _editable_archive_record(self, record: SessionRecord) -> SessionRecord:
        """Validate the exact session folder before an archive mutation."""
        directory = record.directory
        if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
            raise ValueError("Operazione rifiutata: la cartella della sessione è un collegamento.")
        target = directory.resolve(strict=True)
        if target.parent != self.root.resolve(strict=True):
            raise ValueError("Operazione rifiutata: cartella esterna all'archivio o radice dell'archivio.")
        metadata = target / "session.json"
        if metadata.is_symlink():
            raise ValueError("Metadati della sessione non validi.")
        fresh = self.load_record(metadata)
        if fresh.session_id != record.session_id or fresh.directory.resolve() != target:
            raise ValueError("La sessione non corrisponde alla cartella selezionata.")
        if fresh.status in {SessionStatus.RECORDING, SessionStatus.TRANSCRIBING, SessionStatus.SUMMARIZING}:
            raise ValueError("Attendi la fine della registrazione o dell'elaborazione.")
        fresh.directory = target
        return fresh

    def rename(self, record: SessionRecord, title: str) -> None:
        title = " ".join(title.split())
        if not title or len(title) > 200:
            raise ValueError("Inserisci un nome da 1 a 200 caratteri.")
        fresh = self._editable_archive_record(record)
        fresh.title = title
        self.save_record(fresh)
        record.title = title

    def trash(self, record: SessionRecord) -> None:
        fresh = self._editable_archive_record(record)
        # No permanent-delete fallback: if the Recycle Bin fails, keep the data.
        from send2trash import send2trash
        send2trash(str(fresh.directory))

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _write_text(path: Path, value: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(value, encoding="utf-8")
        temporary.replace(path)
