from __future__ import annotations

import gc
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import AppConfig
from .domain import SessionRecord, SessionStatus, TranscriptSegment, merge_segments
from .storage import SessionRepository
from .summarizer import LMStudioSummarizer
from .quality import unexpected_script
from .speakers import SpeakerTimeline
from .participants import Participants, backup_transcript, relabel_transcript

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PipelineCallbacks:
    on_status: Callable[[str, str], None]
    on_partial: Callable[[str, TranscriptSegment], None]
    on_finished: Callable[[SessionRecord, Path | None], None]
    on_error: Callable[[str, str], None]


@dataclass(slots=True)
class _ChunkJob:
    session_id: str
    track: str
    speaker: str
    path: Path
    offset: float


@dataclass(slots=True)
class _FinishJob:
    record: SessionRecord


@dataclass(slots=True)
class _CancelJob:
    record: SessionRecord


@dataclass(slots=True)
class _PreloadJob:
    pass


@dataclass(slots=True)
class _RetranscribeJob:
    record: SessionRecord


@dataclass(slots=True)
class _RecapJob:
    record: SessionRecord


@dataclass(slots=True)
class _DecodedSegment:
    start: float
    end: float
    text: str
    language: str | None = None


@dataclass(slots=True)
class _DecodedInfo:
    language: str | None


class LocalProcessingPipeline:
    """Single-worker queue so Whisper and the local LLM do not compete for VRAM."""

    def __init__(
        self,
        config: AppConfig,
        repository: SessionRepository,
        callbacks: PipelineCallbacks,
    ) -> None:
        self.config = config
        self.repository = repository
        self.callbacks = callbacks
        self._queue: queue.Queue[
            _ChunkJob | _FinishJob | _CancelJob | _PreloadJob | _RetranscribeJob | _RecapJob | None
        ] = queue.Queue()
        self._segments: dict[str, list[TranscriptSegment]] = {}
        self._errors: dict[str, list[str]] = {}
        self._cancelled: set[str] = set()
        self._cancel_lock = threading.Lock()
        self._model = None
        self._model_backend: str | None = None
        self._model_session = None
        self._recording_active = threading.Event()
        self._recap_interrupt = threading.Event()
        self._thread = threading.Thread(target=self._run, name="local-ai", daemon=True)
        self._thread.start()
        if config.preload_whisper:
            self._queue.put(_PreloadJob())

    def submit_chunk(
        self, session_id: str, track: str, speaker: str, path: Path, offset: float
    ) -> None:
        self._queue.put(_ChunkJob(session_id, track, speaker, path, offset))

    def finish(self, record: SessionRecord) -> None:
        self._queue.put(_FinishJob(record))

    def cancel(self, record: SessionRecord) -> None:
        with self._cancel_lock:
            self._cancelled.add(record.session_id)
        self._queue.put(_CancelJob(record))

    def retranscribe(self, record: SessionRecord) -> None:
        self._queue.put(_RetranscribeJob(record))

    def recap(self, record: SessionRecord) -> None:
        self._queue.put(_RecapJob(record))

    def set_recording(self, active: bool) -> None:
        if active:
            self._recording_active.set()
            self._recap_interrupt.set()
        else:
            self._recording_active.clear()

    def close(self, timeout: float = 5.0) -> None:
        self._recap_interrupt.set()
        self._queue.put(None)
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            while True:
                job = self._queue.get()
                try:
                    if job is None:
                        return
                    if isinstance(job, _ChunkJob):
                        self._transcribe_chunk(job)
                    elif isinstance(job, _FinishJob):
                        self._finish_session(job.record)
                    elif isinstance(job, _CancelJob):
                        self._cancel_session(job.record)
                    elif isinstance(job, _RetranscribeJob):
                        self._retranscribe_session(job.record)
                    elif isinstance(job, _RecapJob):
                        self._recap_session(job.record)
                    else:
                        self._load_model()
                        self.callbacks.on_status("global", "Whisper pronto")
                except Exception as exc:
                    LOGGER.exception("Local processing job failed")
                    session_id = (
                        job.session_id
                        if isinstance(job, _ChunkJob)
                        else job.record.session_id
                        if isinstance(job, (_FinishJob, _CancelJob, _RetranscribeJob, _RecapJob))
                        else "global"
                    )
                    self._errors.setdefault(session_id, []).append(str(exc))
                    self.callbacks.on_error(session_id, str(exc))
                    if isinstance(job, (_RetranscribeJob, _RecapJob)):
                        job.record.status = SessionStatus.ERROR
                        job.record.error = str(exc)
                        self.repository.save_record(job.record)
                        self.callbacks.on_finished(job.record, None)
                finally:
                    self._queue.task_done()
        finally:
            self._dispose_model()

    def _load_model(self):
        if self._model is not None:
            return self._model
        if self.config.transcription_backend == "lmstudio_gguf":
            return self._load_gguf_model()
        self.callbacks.on_status("global", "Caricamento Whisper")
        from faster_whisper import WhisperModel

        try:
            self._model = WhisperModel(
                self.config.whisper_model,
                device=self.config.whisper_device,
                compute_type=self.config.whisper_compute_type,
            )
        except Exception:
            if self.config.whisper_device == "cpu":
                raise
            LOGGER.exception("GPU Whisper unavailable; falling back to CPU")
            self.callbacks.on_error(
                "global", "Whisper GPU non disponibile: uso temporaneamente CPU/int8."
            )
            self._model = WhisperModel(self.config.whisper_model, device="cpu", compute_type="int8")
        self._model_backend = "faster_whisper"
        return self._model

    def _load_gguf_model(self, force_cpu: bool = False):
        import transcribe_cpp

        model_path = self._resolve_gguf_model()
        self.callbacks.on_status("global", "Caricamento Whisper GGUF")
        devices = list(transcribe_cpp.backends())
        device = None
        if force_cpu:
            device = next((item for item in devices if item.device_type == "cpu"), None)
        else:
            preference = self.config.transcribe_device_preference.casefold().strip()
            gpu_devices = [item for item in devices if item.device_type == "gpu"]
            device = next(
                (
                    item
                    for item in gpu_devices
                    if preference
                    and preference in f"{item.name} {item.description} {item.kind}".casefold()
                ),
                gpu_devices[0] if gpu_devices else None,
            )
        self._model = transcribe_cpp.Model(str(model_path), device=device)
        self._model_session = self._model.session()
        self._model_backend = "lmstudio_gguf"
        LOGGER.info("Whisper GGUF loaded from %s on %s", model_path, device or "auto")
        return self._model

    def _resolve_gguf_model(self) -> Path:
        configured = self.config.whisper_gguf_path.strip()
        if configured:
            path = Path(configured).expanduser()
            if path.is_file():
                return path
            raise FileNotFoundError(f"Modello Whisper GGUF non trovato: {path}")

        lm_models = Path.home() / ".lmstudio" / "models"
        if lm_models.exists():
            preferred = sorted(
                lm_models.glob("**/whisper-large-v3-turbo-Q8_0.gguf"),
                key=lambda item: ("handy-computer" not in str(item).casefold(), str(item)),
            )
            if preferred:
                path = preferred[0]
                self.config.whisper_gguf_path = str(path)
                self.config.save()
                return path
        raise FileNotFoundError(
            "Nessun whisper-large-v3-turbo-Q8_0.gguf trovato nella libreria di LM Studio. "
            "Imposta whisper_gguf_path nel file di configurazione."
        )

    def _transcribe_chunk(self, job: _ChunkJob) -> None:
        with self._cancel_lock:
            cancelled = job.session_id in self._cancelled
        if cancelled:
            job.path.unlink(missing_ok=True)
            return
        self.callbacks.on_status(job.session_id, "Trascrizione")
        model = self._load_model()
        try:
            output, info = self._run_transcription(model, job.path)
        except RuntimeError as exc:
            detail = str(exc).lower()
            gpu_runtime_error = any(
                marker in detail for marker in ("cuda", "cublas", "cudnn", "curand")
            )
            if self._model_backend == "lmstudio_gguf":
                self.callbacks.on_error(
                    job.session_id,
                    "Whisper GGUF GPU non disponibile: riprovo sulla CPU.",
                )
                self._dispose_model()
                model = self._load_gguf_model(force_cpu=True)
                output, info = self._run_transcription(model, job.path)
            elif self.config.whisper_device == "cpu" or not gpu_runtime_error:
                raise
            else:
                self.callbacks.on_error(
                    job.session_id,
                    "Runtime NVIDIA incompleto: la sessione continua con Whisper CPU/int8.",
                )
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    self.config.whisper_model, device="cpu", compute_type="int8"
                )
                output, info = self._run_transcription(self._model, job.path)
        target = self._segments.setdefault(job.session_id, [])
        for item in output:
            text = item.text.strip()
            if not text:
                continue
            segment = TranscriptSegment(
                start=job.offset + float(item.start),
                end=job.offset + float(item.end),
                text=text,
                speaker=job.speaker,
                track=job.track,
                language=getattr(item, "language", None) or getattr(info, "language", None),
            )
            target.append(segment)
            self.callbacks.on_partial(job.session_id, segment)
        try:
            job.path.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not remove processed chunk %s", job.path)

    def _run_transcription(self, model, path: Path):
        if self._model_backend == "lmstudio_gguf":
            return self._run_gguf_transcription(path)
        output, info = model.transcribe(
            str(path),
            task="transcribe",
            language=self._requested_language(),
            beam_size=3,
            vad_filter=True,
            word_timestamps=False,
            condition_on_previous_text=False,
        )
        return list(output), info

    def _run_gguf_transcription(self, path: Path):
        if self._model_session is None:
            raise RuntimeError("Sessione Whisper GGUF non inizializzata")
        audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
        mono = np.mean(audio, axis=1, dtype=np.float32)
        if mono.size == 0:
            return [], _DecodedInfo(None)
        peak = float(np.max(np.abs(mono)))
        rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64)))
        if peak < 0.003 or rms < 0.00025:
            LOGGER.info("Skipping silent chunk %s (peak %.6f, rms %.6f)", path, peak, rms)
            return [], _DecodedInfo(None)
        if sample_rate != 16_000:
            duration = mono.size / float(sample_rate)
            target_size = max(1, round(duration * 16_000))
            source_x = np.linspace(0.0, 1.0, mono.size, endpoint=False)
            target_x = np.linspace(0.0, 1.0, target_size, endpoint=False)
            mono = np.interp(target_x, source_x, mono).astype(np.float32)
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        from transcribe_cpp import WhisperRunOptions

        # The GGUF path used to send silence/noise to Whisper without any VAD.
        # Silero is already bundled by faster-whisper and runs locally on CPU.
        regions = get_speech_timestamps(mono, VadOptions(
            min_speech_duration_ms=250, min_silence_duration_ms=700,
            speech_pad_ms=250, max_speech_duration_s=20,
        ))
        segments = []
        for region in regions:
            start, end = region["start"], region["end"]
            result = self._model_session.run(
                mono[start:end], language=self._requested_language(), timestamps="segment",
                family=WhisperRunOptions(condition_on_prev_tokens=False, temperature=0.0,
                                         no_speech_thold=0.6),
            )
            language = getattr(result, "language", None)
            if language and language not in {"it", "en", "italian", "english"}:
                LOGGER.warning("Unexpected language %s in %s; audio preserved", language, path)
                segments.append(_DecodedSegment(start / 16000, end / 16000,
                                "[Parlato non affidabile: lingua non riconosciuta come IT/EN]"))
                continue
            for item in result.segments:
                if not item.text.strip():
                    continue
                text = item.text
                if unexpected_script(text):
                    text = "[Parlato non affidabile: verificare l'audio]"
                segments.append(_DecodedSegment(
                    start=(start + max(0, float(item.t0_ms)) * 16) / 16000,
                    end=min(end, start + max(0, float(item.t1_ms)) * 16) / 16000,
                    text=text, language=language,
                ))
        return segments, _DecodedInfo(None)

    def _requested_language(self) -> str | None:
        language = self.config.transcription_language
        return language if language in {"it", "en"} else None

    def _dispose_model(self) -> None:
        session, model = self._model_session, self._model
        self._model_session = None
        self._model = None
        self._model_backend = None
        for resource in (session, model):
            close = getattr(resource, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    LOGGER.debug("Could not close local model resource", exc_info=True)
        gc.collect()

    def _cancel_session(self, record: SessionRecord) -> None:
        self._segments.pop(record.session_id, None)
        self._errors.pop(record.session_id, None)
        try:
            self.repository.cancel(record)
        finally:
            with self._cancel_lock:
                self._cancelled.discard(record.session_id)
        self.callbacks.on_status(record.session_id, "Cancellata")

    def _retranscribe_session(self, record: SessionRecord) -> None:
        """Rebuild only the transcript; a recap always requires a separate request."""
        backup_transcript(record)
        sources: list[tuple[str, str, Path]] = []
        for track, speaker in (
            (
                "mic",
                "Io" if record.source.value == "teams" else "Ambiente",
            ),
            ("system", "Partecipanti remoti"),
        ):
            path = record.directory / f"{track}.flac"
            if path.is_file() and path.stat().st_size > 0:
                sources.append((track, speaker, path))
        if not sources:
            raise FileNotFoundError("Questa sessione non contiene file audio da trascrivere.")

        record.status = SessionStatus.TRANSCRIBING
        record.error = None
        self.repository.save_record(record)
        self._segments[record.session_id] = []
        self._errors[record.session_id] = []
        chunks_dir = record.directory / "reprocess-chunks"
        chunks_dir.mkdir(exist_ok=True)
        try:
            for track, speaker, source_path in sources:
                with sf.SoundFile(str(source_path), mode="r") as source:
                    block_frames = max(
                        1, round(source.samplerate * self.config.transcription_chunk_seconds)
                    )
                    offset = 0.0
                    index = 0
                    while True:
                        audio = source.read(block_frames, dtype="float32", always_2d=True)
                        if len(audio) == 0:
                            break
                        chunk_path = chunks_dir / f"{track}_{index:05d}.flac"
                        sf.write(
                            str(chunk_path),
                            audio,
                            source.samplerate,
                            format="FLAC",
                            subtype="PCM_16",
                        )
                        self._transcribe_chunk(
                            _ChunkJob(
                                record.session_id,
                                track,
                                speaker,
                                chunk_path,
                                offset,
                            )
                        )
                        offset += len(audio) / float(source.samplerate)
                        index += 1
        finally:
            self._remove_empty_chunks_dir(chunks_dir)
        self._finish_session(record)

    def _recap_session(self, record: SessionRecord) -> None:
        transcript_path = record.directory / "transcript.md"
        if not transcript_path.is_file():
            raise FileNotFoundError("Trascrizione mancante: usa prima 'Trascrivi audio'.")
        markdown = transcript_path.read_text(encoding="utf-8").strip()
        if not markdown:
            raise RuntimeError("La trascrizione è vuota.")
        self._summarize_session(record, markdown, [])

    def _finish_session(self, record: SessionRecord) -> None:
        segments = merge_segments(self._segments.pop(record.session_id, []))
        errors = self._errors.pop(record.session_id, [])
        if record.source.value == "teams":
            try:
                timeline = SpeakerTimeline.load(record.directory)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(f"Cronologia nomi non disponibile: {exc}")
                timeline = SpeakerTimeline()
            segments = [timeline.attribute(segment) for segment in segments]
        record.ended_at = record.ended_at or record.started_at
        record.status = SessionStatus.TRANSCRIBING
        self.repository.save_record(record)
        from .domain import transcript_as_markdown

        markdown = transcript_as_markdown(record, segments)
        self.repository.save_transcript(record, segments, markdown)
        if ((record.directory / "participants.json").exists()
                or (record.directory / "voices.json").exists()
                or (record.directory / "live-voices.json").exists()):
            relabel_transcript(record, Participants.load(record.directory), self.repository)
            markdown = (record.directory / "transcript.md").read_text(encoding="utf-8")
        self._remove_empty_chunks_dir(record.directory / "chunks")

        if not segments:
            record.status = SessionStatus.ERROR
            record.error = "; ".join(errors) or "Nessun parlato rilevato."
            self.repository.save_record(record)
            self.callbacks.on_finished(record, None)
            return

        record.status = SessionStatus.COMPLETE
        record.error = "; ".join(errors) or None
        self.repository.save_record(record)
        self.callbacks.on_finished(record, None)

    def _summarize_session(self, record: SessionRecord, markdown: str, errors: list[str]) -> None:
        self._recap_interrupt.clear()
        if self._recording_active.is_set():
            raise RuntimeError("Recap non avviato: registrazione in corso. Richiedilo dall'archivio dopo il meeting.")
        record.status = SessionStatus.SUMMARIZING
        self.repository.save_record(record)
        self.callbacks.on_status(record.session_id, "Libero Whisper dalla GPU")
        self._dispose_model()
        self.callbacks.on_status(record.session_id, "Caricamento modello per il recap richiesto")
        recap_path: Path | None = None
        summarizer: LMStudioSummarizer | None = None
        warnings = list(errors)
        try:
            summarizer = LMStudioSummarizer(
                base_url=self.config.lm_base_url,
                model=self.config.lm_model,
                timeout_seconds=self.config.lm_timeout_seconds,
                chunk_characters=self.config.summary_chunk_characters,
                recap_language=self.config.recap_language,
                context_length=self.config.lm_context_length,
                cancel_event=self._recap_interrupt,
                on_progress=lambda text: self.callbacks.on_status(record.session_id, text),
                checkpoint_dir=record.directory / "recap-work",
                gpu_kv_cache=self.config.lm_gpu_kv_cache,
            )
            recap = summarizer.summarize(markdown, record.title)
            warnings.extend(summarizer.quality_warnings)
            backup_transcript(record)
            recap_path = self.repository.save_recap(record, recap, model_info={
                "model": summarizer.model,
                "context_length": summarizer.context_length,
                "gpu_kv_cache": summarizer.gpu_kv_cache,
                "language": "Italiano",
            })
            (record.directory / "recap-error.txt").unlink(missing_ok=True)
            record.status = SessionStatus.COMPLETE
        except Exception as exc:
            # A missing local LLM must never lose the audio or transcript.
            LOGGER.exception("Summary failed")
            record.status = SessionStatus.COMPLETE
            warning = f"Trascrizione completa; recap non generato: {exc}"
            warnings.append(warning)
            (record.directory / "recap-error.txt").write_text(warning, encoding="utf-8")
            self.callbacks.on_error(record.session_id, warning)
        finally:
            if summarizer and summarizer.cleanup_warning:
                cleanup_warning = f"Modello recap non scaricato dalla VRAM: {summarizer.cleanup_warning}"
                warnings.append(cleanup_warning)
                self.callbacks.on_error(record.session_id, cleanup_warning)
            self.callbacks.on_status(record.session_id, "Ricarico Whisper")
            try:
                if summarizer and summarizer.cleanup_warning:
                    # Preserve live transcription without risking a second GPU allocation.
                    if self.config.transcription_backend == "lmstudio_gguf":
                        self._load_gguf_model(force_cpu=True)
                    else:
                        from faster_whisper import WhisperModel
                        self._model = WhisperModel(self.config.whisper_model, device="cpu", compute_type="int8")
                        self._model_backend = "faster_whisper"
                else:
                    self._load_model()
                self.callbacks.on_status(record.session_id, "Whisper pronto")
            except Exception as exc:
                preload_warning = f"Whisper non precaricato: {exc}"
                warnings.append(preload_warning)
                self.callbacks.on_error(record.session_id, preload_warning)
        record.error = "; ".join(warnings) or None
        self.repository.save_record(record)
        if self.config.delete_audio_after_summary and recap_path:
            for name in ("mic.flac", "system.flac"):
                (record.directory / name).unlink(missing_ok=True)
        self.callbacks.on_finished(record, recap_path)

    @staticmethod
    def _remove_empty_chunks_dir(path: Path) -> None:
        try:
            if path.exists() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass
