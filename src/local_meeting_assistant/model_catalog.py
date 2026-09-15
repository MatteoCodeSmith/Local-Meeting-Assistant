"""Model metadata only: no conversations, inference or automatic quality ranking."""
from __future__ import annotations


def text_models(models: list[dict]) -> list[dict]:
    excluded = ("whisper", "speech", "ecapa", "voxceleb", "embed", "tts")
    return [item for item in models if item.get("type") == "llm" and item.get("key")
            and not any(word in f"{item['key']} {item.get('architecture', '')}".lower()
                        for word in excluded)]


def model_details(info: dict | None) -> str:
    if not info:
        return "Metadati non disponibili. Premi «Leggi modelli installati» con il server locale avviato."
    capabilities = info.get("capabilities") or {}
    reasoning = capabilities.get("reasoning") or {}
    options = reasoning.get("allowed_options", [])
    lines = [str(info.get("display_name") or info["key"]),
             "Uso nell'app: legge la trascrizione e genera il recap in italiano."]
    if info.get("description"):
        lines.append("Descrizione del catalogo: " + str(info["description"])[:600])
    if options:
        if "off" not in options:
            lines.append("Modello con ragionamento obbligatorio: può richiedere più tempo e token di uscita.")
        else:
            lines.append("Ragionamento attivabile/disattivabile; per Qwen 3.5 il recap usa la modalità senza ragionamento.")
    else:
        lines.append("Generazione testuale; il server non dichiara una modalità di ragionamento configurabile.")
    if capabilities.get("vision"):
        lines.append("Supporta anche immagini; questa app gli invia soltanto testo.")
    if capabilities.get("trained_for_tool_use"):
        lines.append("Dichiara supporto agli strumenti; il recap non abilita strumenti né servizi esterni.")
    size = info.get("size_bytes")
    size_label = f"{size / (1024 ** 3):.2f} GiB" if isinstance(size, (int, float)) else "non dichiarato"
    quant = info.get("quantization") or {}
    lines += [
        f"Parametri: {info.get('params_string') or 'non dichiarati'} · architettura: {info.get('architecture') or 'non dichiarata'}",
        f"File su disco: {size_label} · quantizzazione: {quant.get('name') or 'non dichiarata'}",
        f"Contesto massimo dichiarato: {info.get('max_context_length') or 'non dichiarato'} token",
        "Stato: " + ("già caricato nel server" if info.get("loaded_instances") else "installato, non caricato"),
        "Il peso del file non è la VRAM necessaria. Questi sono metadati, non un confronto di qualità.",
    ]
    return "\n".join(lines)
