from __future__ import annotations

import json
import asyncio
import threading
from collections.abc import Callable
from pathlib import Path
from datetime import datetime
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass, field

from .quality import SourceLine, recap_sources
from .topic_recap import TOPIC_SCHEMA, analyze_topics, render_topics


class LMStudioError(RuntimeError):
    pass


def split_text(text: str, max_characters: int) -> list[str]:
    """Split on transcript lines without dropping oversized lines."""
    if max_characters < 500:
        raise ValueError("max_characters deve essere almeno 500")
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for line in text.splitlines(keepends=True):
        if len(line) > max_characters:
            if current:
                chunks.append("".join(current).strip())
                current, current_size = [], 0
            for start in range(0, len(line), max_characters):
                chunks.append(line[start : start + max_characters].strip())
            continue
        if current and current_size + len(line) > max_characters:
            chunks.append("".join(current).strip())
            current, current_size = [], 0
        current.append(line)
        current_size += len(line)
    if current:
        chunks.append("".join(current).strip())
    return [chunk for chunk in chunks if chunk]


@dataclass(slots=True)
class LMStudioSummarizer:
    base_url: str
    model: str
    timeout_seconds: int
    chunk_characters: int
    recap_language: str
    context_length: int = 16_384
    cancel_event: threading.Event | None = None
    on_progress: Callable[[str], None] | None = None
    checkpoint_dir: Path | None = None
    gpu_kv_cache: bool = False
    last_request_stats: dict = field(init=False, default_factory=dict)
    cleanup_warning: str | None = field(init=False, default=None)
    quality_warnings: list[str] = field(init=False, default_factory=list)

    def __post_init__(self):
        target = urlsplit(self.base_url)
        if target.scheme not in {"http", "https"} or target.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise LMStudioError("Per privacy il recap accetta soltanto un server sul PC locale (localhost / 127.0.0.1 / ::1).")

    def summarize(self, transcript: str, title: str) -> str:
        self._check_cancelled()
        sources, self.quality_warnings, blocked = recap_sources(transcript)
        if blocked:
            raise LMStudioError(" ".join(self.quality_warnings))

        model_instance: str | None = None
        self.cleanup_warning = None
        try:
            model_instance = self._load_model_for_recap()
            return self._summarize_with_model(model_instance, sources, title)
        finally:
            if model_instance:
                try:
                    self._unload_model(model_instance)
                except LMStudioError as exc:
                    # Preserve a recap that was generated successfully, but expose cleanup failure.
                    self.cleanup_warning = str(exc)

    def _summarize_with_model(self, model: str, sources: list[SourceLine], title: str) -> str:
        # Leave space for structured output, instructions and the consolidation pass.
        limit = min(self.chunk_characters, max(2000, (self.context_length - 6500) * 2))
        run_dir = None
        if self.checkpoint_dir:
            run_dir = self.checkpoint_dir / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            run_dir.mkdir(parents=True, exist_ok=True)
        call_number = 0

        def chat(*args, **kwargs):
            nonlocal call_number
            self._check_cancelled()
            call_number += 1
            if self.on_progress:
                self.on_progress(f"Recap richiesto · analisi {call_number}")
            result = self._chat(*args, **kwargs)
            if run_dir:
                (run_dir / f"response-{call_number:03d}.json").write_text(result, encoding="utf-8")
                (run_dir / f"stats-{call_number:03d}.json").write_text(
                    json.dumps(self.last_request_stats, indent=2), encoding="utf-8")
            return result
        try:
            document = analyze_topics(chat, model, sources, limit, "Italiano",
                                      merge_limit=max(2000, (self.context_length - 6500) * 2),
                                      warnings=self.quality_warnings,
                                      full_context_limit=max(0, int((self.context_length - 6500) * 2.3)))
        except ValueError as exc:
            raise LMStudioError(str(exc)) from exc
        result = render_topics(document, title, sources, self.quality_warnings)
        if run_dir:
            (run_dir / "recap.md").write_text(result, encoding="utf-8")
        return result

    def _discover_model(self, models: list[dict[str, object]] | None = None) -> str:
        if models is None:
            payload = self._native_request("GET", "/models")
            models = list(payload.get("models", []))
        if not models:
            raise LMStudioError("LM Studio non ha alcun modello locale disponibile.")
        excluded_markers = ("whisper", "embed", "embedding", "tts", "speech", "ecapa", "voxceleb")
        candidates = [
            item
            for item in models
            if item.get("type") == "llm"
            and item.get("key")
            and not any(
                marker in f"{item.get('key', '')} {item.get('architecture', '')}".lower()
                for marker in excluded_markers
            )
        ]
        if not candidates:
            raise LMStudioError("Nessun modello LLM testuale trovato in LM Studio.")
        candidates.sort(key=lambda item: int(item.get("size_bytes") or 2**63))
        return str(candidates[0]["key"])

    def _load_model_for_recap(self) -> str:
        self._check_cancelled()
        payload = self._native_request("GET", "/models")
        models = list(payload.get("models", []))
        requested = self.model or self._discover_model(models)
        info = next(
            (
                item
                for item in models
                if item.get("key") == requested
                or any(
                    instance.get("id") == requested for instance in item.get("loaded_instances", [])
                )
            ),
            None,
        )
        if info is None:
            raise LMStudioError(f"Modello LM Studio non trovato: {requested}")

        # Replace GUI-loaded instances, which may use a needlessly large context, with one
        # short-lived low-memory instance dedicated to the recap.
        for instance in info.get("loaded_instances", []):
            instance_id = instance.get("id")
            if instance_id:
                self._unload_model(str(instance_id))

        loaded = self._native_request(
            "POST",
            "/models/load",
            {
                "model": info["key"],
                "context_length": self.context_length,
                "flash_attention": True,
                "offload_kv_cache_to_gpu": self.gpu_kv_cache,
            },
        )
        instance_id = loaded.get("model_instance_id") or loaded.get("instance_id")
        if not instance_id:
            raise LMStudioError("LM Studio non ha restituito l'ID del modello caricato.")
        return str(instance_id)

    def _unload_model(self, instance_id: str) -> None:
        self._native_request("POST", "/models/unload", {"instance_id": instance_id})

    def _chat(self, model: str, *, system: str, user: str) -> str:
        self._check_cancelled()
        # Compact turns retain IDs, timestamps and names while avoiding repeated JSON keys.
        # This leaves more of the context window for the conversation itself.
        try:
            compact = json.loads(user)
            for key in ("dialogue", "original_sources"):
                rows = compact.get(key)
                if isinstance(rows, list):
                    compact[key] = "\n".join(
                        f"T{r['id']} {r['time']} {r['speaker']}: {r['text']}" for r in rows
                    )
            user = json.dumps(compact, ensure_ascii=False)
        except (ValueError, KeyError, AttributeError, TypeError):
            pass
        if "qwen3.5" in model.lower():
            # Native API explicitly supports reasoning control. Unknown template kwargs
            # on the compatibility endpoint may be ignored by the server.
            url = self.base_url.rstrip("/").removesuffix("/v1") + "/api/v1/chat"
            response = self._cancellable_request(url, {
                "model": model, "input": user,
                "system_prompt": system + "\nSchema JSON da rispettare:\n" + json.dumps(TOPIC_SCHEMA),
                "temperature": 0, "max_output_tokens": 5000,
                "reasoning": "off", "store": False, "integrations": [],
            })
            self.last_request_stats = response.get("stats", {})
            result = "\n".join(item["content"] for item in response.get("output", [])
                               if item.get("type") == "message" and isinstance(item.get("content"), str)).strip()
            if result.startswith("```") and result.endswith("```"):
                result = result.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            if not result:
                raise LMStudioError("Il modello non ha restituito testo per il recap.")
            return result
        payload = self._request(
            "POST",
            "/chat/completions",
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.0,
                "max_tokens": 5000,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "meeting_topics",
                        "strict": True,
                        "schema": TOPIC_SCHEMA,
                    },
                },
                "stream": False,
            },
        )
        self.last_request_stats = payload.get("usage", {})
        try:
            if payload["choices"][0].get("finish_reason") == "length":
                raise LMStudioError("Risposta del modello troncata: ritento questa sezione.")
            return str(payload["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LMStudioError("Risposta chat di LM Studio non valida.") from exc

    def _request(
        self, method: str, endpoint: str, body: dict[str, object] | None = None
    ) -> dict[str, object]:
        url = self.base_url.rstrip("/") + endpoint
        if self.cancel_event is not None and endpoint == "/chat/completions":
            return self._cancellable_request(url, body)
        return self._request_url(method, url, body)

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise InterruptedError("Recap interrotto per dare priorità a Whisper. Richiedilo nuovamente dall'archivio.")

    def _cancellable_request(self, url: str, body: dict | None) -> dict:
        """Cancel async network I/O even during prefill or a stalled response body.

        Native unload is deliberately NOT cancelled and completes before Whisper reload.
        """
        import httpx
        self._check_cancelled()

        async def perform():
            async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                task = asyncio.create_task(client.post(url, json=body,
                    headers={"Authorization": "Bearer lm-studio"}))
                try:
                    while not task.done():
                        self._check_cancelled()
                        await asyncio.wait({task}, timeout=0.1)
                    self._check_cancelled()
                    response = await task
                    if response.status_code >= 400:
                        raise LMStudioError(f"LM Studio HTTP {response.status_code}: {response.text}")
                    return response.json()
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        try:
            return asyncio.run(perform())
        except (httpx.HTTPError, ValueError) as exc:
            self._check_cancelled()
            raise LMStudioError(f"Richiesta recap non riuscita: {exc}") from exc

    def _native_request(
        self, method: str, endpoint: str, body: dict[str, object] | None = None
    ) -> dict[str, object]:
        base = self.base_url.rstrip("/")
        base = base.removesuffix("/v1")
        return self._request_url(method, f"{base}/api/v1{endpoint}", body)

    def _request_url(
        self, method: str, url: str, body: dict[str, object] | None
    ) -> dict[str, object]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Authorization": "Bearer lm-studio"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LMStudioError(f"LM Studio HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LMStudioError(f"LM Studio non raggiungibile su {url}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LMStudioError("LM Studio ha restituito JSON non valido.") from exc
