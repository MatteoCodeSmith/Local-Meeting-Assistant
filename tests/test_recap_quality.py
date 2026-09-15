import json
from unittest.mock import patch

import pytest

from local_meeting_assistant.quality import recap_sources, SourceLine
from local_meeting_assistant.summarizer import LMStudioError, LMStudioSummarizer
from local_meeting_assistant.topic_recap import source_batches, analyze_topics


def summarizer():
    return LMStudioSummarizer("http://localhost:1234/v1", "gemma", 30, 6000, "Italiano")


def document(refs=None):
    return {
        "overview": "Il dialogo riguarda la verifica del prototipo.",
        "topics": [
            {
                "title": "Verifica del prototipo",
                "analysis": "Il problema è il consumo durante i test. "
                "L'acquisto resta in sospeso in attesa di approfondire i risultati.",
                "outcome": "L'acquisto non è approvato.",
                "source_ids": refs or [1, 2],
                "actions": [],
                "open_questions": ["Da cosa dipende il consumo?"],
            }
        ],
    }


def test_contextual_analysis_not_verbatim_sentence_list():
    text = (
        "**00:01 — Io:** Approviamo l'acquisto del prototipo?\n**00:03 — Luca:** No, prima i test."
    )
    with patch.object(LMStudioSummarizer, "_chat", return_value=json.dumps(document())) as chat:
        result = summarizer()._summarize_with_model("gemma", recap_sources(text)[0], "Test")
    assert "## Quadro generale" in result and "## Verifica del prototipo" in result
    assert "L'acquisto non è approvato." in result and "00:03" in result
    assert "No, prima i test." in chat.call_args.kwargs["user"]
    assert result.index("Il problema") < result.index("Passaggi di riferimento")


@pytest.mark.parametrize("refs", [[999], [[1]], [True]])
def test_invalid_source_ids_are_removed_with_warning_not_fatal(refs):
    generator = summarizer()
    with patch.object(LMStudioSummarizer, "_chat", return_value=json.dumps(document(refs))) as chat:
        result = generator._summarize_with_model(
                "gemma", recap_sources("Una frase valida di prova.")[0], "Test"
            )
    assert "Verifica del prototipo" in result
    assert "Riferimenti non validi rimossi" in result
    assert "non validati" in result
    assert chat.call_count >= 2


def test_suspicious_transcript_blocks_before_loading_vram():
    text = "\n".join(f"**00:{i:02d} — Io:** I'm going to make some butter." for i in range(10))
    with patch.object(LMStudioSummarizer, "_load_model_for_recap") as load:
        with pytest.raises(LMStudioError, match="sospetta"):
            summarizer().summarize(text, "Test")
        load.assert_not_called()


def test_bilingual_dialogue_keeps_short_negations():
    rows, _, blocked = recap_sources(
        "**00:00 — Io:** Dobbiamo inviare il report.\n"
        "**00:10 — Remoto:** No.\n"
        "**00:12 — Remoto:** We need to check the test results first."
    )
    assert len(rows) == 3 and rows[1].text == "No." and not blocked


def test_header_only_transcript_is_not_a_meeting():
    rows, _, blocked = recap_sources("# Trascrizione\n\n- Origine: teams\n")
    assert not rows and blocked


def test_model_unloads_on_malformed_response():
    with (
        patch.object(LMStudioSummarizer, "_load_model_for_recap", return_value="instance"),
        patch.object(LMStudioSummarizer, "_unload_model") as unload,
        patch.object(LMStudioSummarizer, "_chat", return_value="Invalid content"),
    ):
        with pytest.raises(LMStudioError):
            summarizer().summarize("Una frase valida di lunghezza sufficiente.", "Test")
        unload.assert_called_once_with("instance")


def test_structured_zero_temperature_request():
    with patch.object(
        LMStudioSummarizer,
        "_request",
        return_value={
            "choices": [{"message": {"content": json.dumps(document())}, "finish_reason": "stop"}]
        },
    ) as request:
        summarizer()._chat("gemma", system="rules", user="data")
    body = request.call_args.args[2]
    assert (
        body["temperature"] == 0
        and "topics" in body["response_format"]["json_schema"]["schema"]["properties"]
    )


def test_batches_overlap_without_losing_turns():
    rows = [SourceLine(i, "00:00", "Io", "parole " * 20) for i in range(12)]
    batches = source_batches(rows, 800)
    assert {row["id"] for batch in batches for row in batch} == set(range(12))
    assert batches[0][-1] in batches[1]
    with pytest.raises(ValueError, match="troppo lungo"):
        source_batches([SourceLine(0, "00:00", "Io", "x" * 2000)], 1000)


def test_consolidation_sees_original_evidence_and_previous_topics():
    rows = [SourceLine(i, "00:00", "Io", "parole " * 20) for i in range(8)]
    calls = []

    def chat(model, *, system, user):
        payload = json.loads(user)
        calls.append(payload)
        ids = [r["id"] for r in payload.get("dialogue", payload.get("original_sources", []))]
        return json.dumps(document(ids))

    doc = analyze_topics(chat, "gemma", rows, 900, "Italiano")
    assert any("chronological_analyses" in call for call in calls)
    assert set(doc["topics"][0]["source_ids"]) == set(range(8))


def test_integral_analysis_keeps_all_sources_including_final_correction():
    rows = [SourceLine(i, "00:00", "Io", "testo " * 20) for i in range(8)]
    calls = []
    def chat(model, *, system, user):
        payload = json.loads(user)
        calls.append(payload)
        return json.dumps(document([0, 7]))
    analyze_topics(chat, "qwen", rows, 800, "Italiano", full_context_limit=10000)
    assert len(calls) == 2
    assert len(calls[0]["dialogue"]) == 8
    assert len(calls[1]["original_sources"]) == 8
    assert not any("chronological_analyses" in call for call in calls)


def test_qwen_uses_native_reasoning_off_and_no_stored_chat():
    generator = summarizer()
    with patch.object(LMStudioSummarizer, "_cancellable_request", return_value={
        "output": [{"type": "message", "content": json.dumps(document())}],
        "stats": {"reasoning_output_tokens": 0},
    }) as request:
        generator._chat("qwen3.5-9b", system="regole", user="dati")
    assert request.call_args.args[0].endswith("/api/v1/chat")
    body = request.call_args.args[1]
    assert body["reasoning"] == "off" and body["store"] is False
    assert body["integrations"] == []
    assert generator.last_request_stats["reasoning_output_tokens"] == 0
