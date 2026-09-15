from local_meeting_assistant.model_catalog import model_details, text_models


def test_catalog_shows_metadata_without_quality_ranking():
    info = {"key": "example-local", "display_name": "Modello di prova", "type": "llm",
            "size_bytes": 5 * 1024**3, "params_string": "8B", "architecture": "example",
            "quantization": {"name": "Q4_K_M"}, "max_context_length": 32768,
            "capabilities": {"reasoning": {"allowed_options": ["on"]}, "vision": True}}
    result = model_details(info)
    assert "5.00 GiB" in result and "Q4_K_M" in result and "32768" in result
    assert "ragionamento obbligatorio" in result and "non un confronto di qualità" in result


def test_audio_models_are_excluded_and_missing_metadata_is_explicit():
    models = [{"type": "llm", "key": "whisper"}, {"type": "llm", "key": "speechbrain-ecapa"},
              {"type": "llm", "key": "text-example"}]
    assert [row["key"] for row in text_models(models)] == ["text-example"]
    assert "non disponibili" in model_details(None)


def test_remote_ai_endpoint_is_rejected_before_network_access():
    import pytest
    from local_meeting_assistant.summarizer import LMStudioSummarizer, LMStudioError
    with pytest.raises(LMStudioError, match="privacy"):
        LMStudioSummarizer("https://example.com/v1", "model", 60, 12000, "Italiano")


def test_each_recap_has_its_own_model_metadata_and_file(tmp_path):
    from local_meeting_assistant.domain import SessionSource
    from local_meeting_assistant.storage import SessionRepository
    repo = SessionRepository(tmp_path)
    record = repo.create(SessionSource.MANUAL, "Dati fittizi")
    repo.save_recap(record, "Recap sintetico A", model_info={"model": "modello-A"})
    repo.save_recap(record, "Recap sintetico B", model_info={"model": "modello-B"})
    versions = repo.list_recaps(record)
    assert len(versions) == 2
    assert {row["model"] for row in versions} == {"modello-A", "modello-B"}
    assert {row["path"].read_text(encoding="utf-8").strip() for row in versions} == {
        "Recap sintetico A", "Recap sintetico B"}
    assert (record.directory / "recap.md").read_text(encoding="utf-8").strip() == "Recap sintetico B"
