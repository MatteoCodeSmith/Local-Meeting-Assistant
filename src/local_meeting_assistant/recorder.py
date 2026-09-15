from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class RecorderCallbacks:
    on_level: Callable[[str, float], None]
    on_chunk: Callable[[str, Path, float], None]
    on_error: Callable[[str, str], None]
    on_track_state: Callable[[str, bool], None]
    on_device: Callable[[str, str], None]
    on_audio_span: Callable[[str, float, float, float], None] | None = None
    on_voice_audio: Callable[[str, Any, int, float], None] | None = None


@dataclass(slots=True, frozen=True)
class _InputDevice:
    index: int
    channels: int
    sample_rate: int
    endpoint_id: str
    name: str


class _TrackCapture(threading.Thread):
    def __init__(
        self,
        *,
        track: str,
        device: Any,
        output_path: Path,
        chunks_dir: Path,
        sample_rate: int,
        block_seconds: float,
        chunk_seconds: int,
        callbacks: RecorderCallbacks,
        backend: str = "soundcard",
        channels: int | None = None,
        device_poll_seconds: float = 1.0,
    ) -> None:
        super().__init__(name=f"capture-{track}", daemon=True)
        self.track = track
        self.device = device
        self.output_path = output_path
        self.chunks_dir = chunks_dir
        self.sample_rate = sample_rate
        self.block_frames = max(256, int(sample_rate * block_seconds))
        self.chunk_frames = max(self.block_frames, int(sample_rate * chunk_seconds))
        self.callbacks = callbacks
        self.backend = backend
        self.channels = channels
        self.device_poll_seconds = max(0.25, device_poll_seconds)
        self.stop_event = threading.Event()
        self._chunk_index = 0

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        pythoncom = None
        try:
            try:
                import pythoncom as _pythoncom

                pythoncom = _pythoncom
                pythoncom.CoInitialize()
            except ImportError:
                pass
            import numpy as np
            import soundfile as sf

            blocks = self._iter_blocks()
            try:
                first = self._normalise(next(blocks), np)
            except StopIteration:
                return
            if first.size == 0:
                raise RuntimeError("Il dispositivo non ha restituito campioni audio.")
            chunk_parts: list[Any] = []
            chunk_frame_count = 0
            written_frames = 0
            voice_parts, voice_frames, voice_offset = [], 0, 0.0
            channels = int(first.shape[1])
            with sf.SoundFile(
                self.output_path,
                mode="w",
                samplerate=self.sample_rate,
                channels=channels,
                format="FLAC",
                subtype="PCM_16",
            ) as output:
                self.callbacks.on_track_state(self.track, True)
                for raw in self._prepend(first, blocks):
                    if self.stop_event.is_set():
                        break
                    pending = self._normalise(raw, np)
                    if pending.size == 0:
                        time.sleep(0.02)
                        continue
                    output.write(pending)
                    if self.callbacks.on_voice_audio:
                        voice_parts.append(pending.copy())
                        voice_frames += len(pending)
                        if voice_frames >= self.sample_rate * 3:
                            self._send_voice_audio(voice_parts, voice_offset, np)
                            voice_offset += voice_frames / self.sample_rate
                            voice_parts, voice_frames = [], 0
                    if self.callbacks.on_audio_span:
                        self.callbacks.on_audio_span(
                            self.track, written_frames / self.sample_rate,
                            (written_frames + len(pending)) / self.sample_rate,
                            time.monotonic() - len(pending) / self.sample_rate,
                        )
                    written_frames += len(pending)
                    level = float(np.sqrt(np.mean(np.square(pending), dtype=np.float64)))
                    self.callbacks.on_level(self.track, min(1.0, level * 12.0))
                    chunk_parts.append(pending.copy())
                    chunk_frame_count += len(pending)

                    while chunk_frame_count >= self.chunk_frames:
                        chunk_parts, chunk_frame_count = self._flush_chunk(
                            chunk_parts, chunk_frame_count, sf, np, final=False
                        )

                if chunk_parts:
                    self._flush_chunk(chunk_parts, chunk_frame_count, sf, np, final=True)
                if voice_frames >= self.sample_rate * 0.8:
                    self._send_voice_audio(voice_parts, voice_offset, np)
        except Exception as exc:
            LOGGER.exception("Audio capture failed for %s", self.track)
            self.callbacks.on_error(self.track, str(exc))
        finally:
            self.callbacks.on_level(self.track, 0.0)
            self.callbacks.on_track_state(self.track, False)
            if pythoncom is not None:
                pythoncom.CoUninitialize()

    def _iter_blocks(self):
        if self.backend == "sounddevice":
            yield from self._iter_sounddevice_blocks()
            return

        with self.device.recorder(samplerate=self.sample_rate) as source:
            while not self.stop_event.is_set():
                yield source.record(numframes=self.block_frames)

    def _send_voice_audio(self, parts, offset, np):
        try:
            self.callbacks.on_voice_audio(self.track, np.concatenate(parts, axis=0), self.sample_rate, offset)
        except Exception:
            # Optional voice recognition must never terminate audio capture.
            LOGGER.warning("Optional voice buffer skipped")

    def _iter_sounddevice_blocks(self):
        """Keep the microphone alive and reopen it when Windows changes its default."""
        import numpy as np
        import sounddevice as sd

        resolver = self.device
        last_reported_error = ""
        while not self.stop_event.is_set():
            try:
                candidates: list[_InputDevice] = resolver()
            except Exception as exc:
                detail = f"Microfono predefinito non disponibile: {exc}. Riprovo."
                if detail != last_reported_error:
                    self.callbacks.on_error(self.track, detail)
                    last_reported_error = detail
                self.stop_event.wait(self.device_poll_seconds)
                continue

            active_endpoint = candidates[0].endpoint_id if candidates else ""
            stream_opened = False
            candidate_errors: list[str] = []
            for candidate in candidates:
                if self.stop_event.is_set():
                    return
                try:
                    source_frames = max(
                        256,
                        round(self.block_frames * candidate.sample_rate / self.sample_rate),
                    )
                    with sd.InputStream(
                        device=candidate.index,
                        samplerate=candidate.sample_rate,
                        channels=candidate.channels,
                        dtype="float32",
                        blocksize=source_frames,
                    ) as source:
                        stream_opened = True
                        last_reported_error = ""
                        self.callbacks.on_device(self.track, candidate.name)
                        next_poll = time.monotonic() + self.device_poll_seconds
                        while not self.stop_event.is_set():
                            data, overflowed = source.read(source_frames)
                            if overflowed:
                                LOGGER.warning("Input overflow on %s", self.track)
                            mono = np.asarray(data, dtype=np.float32)
                            if mono.ndim == 2 and mono.shape[1] > 1:
                                mono = np.mean(mono, axis=1, dtype=np.float32)
                            mono = mono.reshape(-1, 1)
                            if candidate.sample_rate != self.sample_rate and len(mono) > 1:
                                target_size = max(
                                    1,
                                    round(len(mono) * self.sample_rate / candidate.sample_rate),
                                )
                                source_x = np.linspace(0.0, 1.0, len(mono), endpoint=False)
                                target_x = np.linspace(0.0, 1.0, target_size, endpoint=False)
                                mono = np.interp(target_x, source_x, mono[:, 0]).astype(np.float32)[
                                    :, None
                                ]
                            yield mono

                            if time.monotonic() >= next_poll:
                                next_poll = time.monotonic() + self.device_poll_seconds
                                updated = resolver()
                                updated_endpoint = updated[0].endpoint_id if updated else ""
                                if updated_endpoint and updated_endpoint != active_endpoint:
                                    self.callbacks.on_device(
                                        self.track, f"Cambio dispositivo: {updated[0].name}"
                                    )
                                    break
                        if self.stop_event.is_set():
                            return
                        break
                except Exception as exc:
                    candidate_errors.append(f"{candidate.name}: {exc}")
                    LOGGER.warning(
                        "Could not open microphone candidate %s", candidate.name, exc_info=True
                    )
            if stream_opened:
                continue
            detail = "Impossibile aprire il microfono di Windows"
            if candidate_errors:
                detail += f": {candidate_errors[-1]}"
            detail += ". Riprovo automaticamente."
            if detail != last_reported_error:
                self.callbacks.on_error(self.track, detail)
                last_reported_error = detail
            self.stop_event.wait(self.device_poll_seconds)

    @staticmethod
    def _prepend(first: Any, blocks):
        yield first
        yield from blocks

    @staticmethod
    def _normalise(data: Any, np: Any) -> Any:
        array = np.asarray(data, dtype=np.float32)
        if array.ndim == 1:
            array = array[:, None]
        return np.nan_to_num(array, copy=False)

    def _flush_chunk(
        self,
        parts: list[Any],
        frame_count: int,
        sf: Any,
        np: Any,
        *,
        final: bool,
    ) -> tuple[list[Any], int]:
        combined = np.concatenate(parts, axis=0)
        take = len(combined) if final else self.chunk_frames
        current = combined[:take]
        remainder = combined[take:]
        offset = self._chunk_index * (self.chunk_frames / self.sample_rate)
        chunk_path = self.chunks_dir / f"{self.track}_{self._chunk_index:05d}.flac"
        sf.write(chunk_path, current, self.sample_rate, format="FLAC", subtype="PCM_16")
        self._chunk_index += 1
        self.callbacks.on_chunk(self.track, chunk_path, offset)
        if len(remainder):
            return [remainder], len(remainder)
        return [], 0


class AudioRecorder:
    """Capture the current Windows microphone and render loopback separately."""

    def __init__(self, callbacks: RecorderCallbacks) -> None:
        self.callbacks = callbacks
        self._tracks: dict[str, _TrackCapture] = {}

    @property
    def running(self) -> bool:
        return any(track.is_alive() for track in self._tracks.values())

    def start(
        self,
        session_dir: Path,
        *,
        sample_rate: int,
        block_seconds: float,
        chunk_seconds: int,
        microphone: bool,
        system_audio: bool,
        microphone_name: str = "",
        speaker_name: str = "",
        follow_default_microphone: bool = True,
        device_poll_seconds: float = 1.0,
    ) -> list[str]:
        if self.running:
            raise RuntimeError("Una registrazione e gia in corso.")
        import soundcard as sc

        chunks_dir = session_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        devices: dict[str, tuple[Any, str, int | None, int]] = {}
        errors: list[str] = []

        speaker = None
        if system_audio or speaker_name:
            try:
                speaker = sc.get_speaker(speaker_name) if speaker_name else sc.default_speaker()
            except Exception as exc:
                errors.append(f"uscita audio: {exc}")

        if microphone:
            try:
                resolver = lambda: self._sounddevice_inputs(
                    microphone_name,
                    sample_rate,
                    follow_default=follow_default_microphone,
                )
                resolver()
                devices["mic"] = (resolver, "sounddevice", 1, sample_rate)
            except Exception as exc:
                errors.append(f"microfono: {exc}")

        if system_audio:
            try:
                if speaker is None:
                    raise RuntimeError("Nessuna uscita audio predefinita.")
                devices["system"] = (
                    sc.get_microphone(id=str(speaker.name), include_loopback=True),
                    "soundcard",
                    None,
                    sample_rate,
                )
            except Exception as exc:
                errors.append(f"audio PC: {exc}")

        if not devices:
            raise RuntimeError("Impossibile aprire dispositivi audio: " + "; ".join(errors))
        for error in errors:
            self.callbacks.on_error("setup", error)

        self._tracks = {
            name: _TrackCapture(
                track=name,
                device=device_info[0],
                output_path=session_dir / f"{name}.flac",
                chunks_dir=chunks_dir,
                sample_rate=device_info[3],
                block_seconds=block_seconds,
                chunk_seconds=chunk_seconds,
                callbacks=self.callbacks,
                backend=device_info[1],
                channels=device_info[2],
                device_poll_seconds=device_poll_seconds,
            )
            for name, device_info in devices.items()
        }
        for track in self._tracks.values():
            track.start()
        return list(self._tracks)

    @staticmethod
    def _sounddevice_inputs(
        name: str, requested_rate: int, *, follow_default: bool
    ) -> list[_InputDevice]:
        import sounddevice as sd

        all_devices = sd.query_devices()
        host_apis = sd.query_hostapis()
        endpoint_id = "configured"
        target_name = name.strip()
        if not target_name and follow_default:
            endpoint_id, target_name = AudioRecorder._windows_default_microphone()
        target_key = AudioRecorder._normalise_device_name(target_name)
        candidates = [
            (index, device)
            for index, device in enumerate(all_devices)
            if int(device.get("max_input_channels", 0)) > 0
            and (
                not target_key
                or target_key in AudioRecorder._normalise_device_name(str(device.get("name", "")))
                or AudioRecorder._normalise_device_name(str(device.get("name", ""))) in target_key
            )
        ]
        if target_name and not candidates:
            raise RuntimeError(f"Microfono non trovato: {target_name}")
        if not candidates:
            default_input = int(sd.default.device[0])
            candidates = [
                (index, device)
                for index, device in enumerate(all_devices)
                if index == default_input and int(device.get("max_input_channels", 0)) > 0
            ]
        api_priority = {"windows wasapi": 0, "windows directsound": 1, "mme": 2}
        candidates.sort(
            key=lambda item: (
                api_priority.get(str(host_apis[int(item[1]["hostapi"])]["name"]).casefold(), 3),
                item[0],
            )
        )
        result: list[_InputDevice] = []
        for index, device in candidates:
            rates = [requested_rate, int(device["default_samplerate"])]
            for rate in dict.fromkeys(rates):
                try:
                    sd.check_input_settings(
                        device=index, channels=1, samplerate=rate, dtype="float32"
                    )
                except Exception:
                    continue
                result.append(
                    _InputDevice(
                        index=index,
                        channels=1,
                        sample_rate=rate,
                        endpoint_id=endpoint_id or target_key or str(index),
                        name=str(device["name"]),
                    )
                )
                break
        if not result:
            raise RuntimeError(f"Nessun formato audio valido per: {target_name or 'predefinito'}")
        return result

    @staticmethod
    def _windows_default_microphone() -> tuple[str, str]:
        from pycaw.pycaw import AudioUtilities, EDataFlow, ERole

        enumerator = AudioUtilities.GetDeviceEnumerator()
        endpoint = enumerator.GetDefaultAudioEndpoint(
            EDataFlow.eCapture.value, ERole.eMultimedia.value
        )
        device = AudioUtilities.CreateDevice(endpoint)
        if device is None:
            raise RuntimeError("Windows non espone un microfono predefinito")
        return str(device.id), str(device.FriendlyName)

    @staticmethod
    def _normalise_device_name(value: str) -> str:
        return "".join(character for character in value.casefold() if character.isalnum())

    def stop(self, timeout: float = 10.0) -> None:
        tracks = list(self._tracks.values())
        for track in tracks:
            track.stop()
        deadline = time.monotonic() + timeout
        for track in tracks:
            track.join(max(0.0, deadline - time.monotonic()))
        still_running = [track.track for track in tracks if track.is_alive()]
        if still_running:
            raise RuntimeError("Timeout chiusura audio: " + ", ".join(still_running))
        self._tracks.clear()
