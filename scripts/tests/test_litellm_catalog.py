"""Regression tests for the LiteLLM catalog importer.

Run with:

    python3 -m pytest scripts/tests/test_litellm_catalog.py

Root `pytest.ini` deliberately restricts default collection to `python/sdk/tests`,
so this file is invoked explicitly.
"""

from __future__ import annotations

import os
import runpy
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "litellm_catalog.py"
catalog = runpy.run_path(str(SCRIPT))

CatalogError = catalog["CatalogError"]
classify = catalog["classify"]
capacity = catalog["capacity"]
build_models = catalog["build_models"]
build_provider = catalog["build_provider"]
merge_provider = catalog["merge_provider"]
load_yaml_strict = catalog["load_yaml_strict"]
write_atomic = catalog["write_atomic"]
render_yaml = catalog["render_yaml"]
format_report = catalog["format_report"]


def entry(name: str, *, mode: str | None = None, wildcard: bool = False, **model_info: Any) -> dict[str, Any]:
    info: dict[str, Any] = dict(model_info)
    if mode is not None:
        info["mode"] = mode
    if wildcard:
        info["wildcard"] = True
    return {"model_name": name, "model_info": info, "litellm_params": {}}


def admit(name: str, **kwargs: Any) -> str:
    model_id, reason = classify(entry(name, **kwargs), allow_wildcards=False, allow_other_protocols=False)
    assert reason == "", f"{name} was rejected: {reason}"
    return model_id


def reject(name: str, **kwargs: Any) -> str:
    model_id, reason = classify(entry(name, **kwargs), allow_wildcards=False, allow_other_protocols=False)
    assert model_id == "", f"{name} was unexpectedly admitted"
    return reason


# --- classification is capability metadata, not name substring guessing --------


def test_wildcard_route_placeholders_are_excluded() -> None:
    reason = reject("alibaba-us-east-1/gpt-5.2-pro-2025-12-11", wildcard=True)
    assert "wildcard route placeholder" in reason


def test_wildcard_placeholders_can_be_opted_into() -> None:
    model_id, reason = classify(
        entry("vendor/some-model", wildcard=True),
        allow_wildcards=True,
        allow_other_protocols=False,
    )
    assert (model_id, reason) == ("vendor/some-model", "")


def test_literal_wildcard_pattern_is_always_excluded() -> None:
    model_id, reason = classify(
        entry("meta/*"), allow_wildcards=True, allow_other_protocols=False
    )
    assert model_id == ""
    assert "wildcard pattern" in reason


@pytest.mark.parametrize("mode", ["embedding", "rerank", "image_generation", "tts", "audio_transcription", "moderation", "guard"])
def test_non_chat_modes_are_excluded_even_with_a_chat_sounding_name(mode: str) -> None:
    reason = reject("vendor/chat-looks-fine", mode=mode)
    assert f"mode={mode}" in reason


def test_chat_mode_is_admitted() -> None:
    assert admit("vendor/real-chat-model", mode="chat") == "vendor/real-chat-model"


def test_responses_mode_needs_its_own_route_by_default() -> None:
    assert "needs its own api route" in reject("vendor/responses-model", mode="responses")


def test_responses_mode_can_be_opted_into() -> None:
    model_id, reason = classify(
        entry("vendor/responses-model", mode="responses"),
        allow_wildcards=False,
        allow_other_protocols=True,
    )
    assert (model_id, reason) == ("vendor/responses-model", "")


def test_name_heuristic_applies_only_when_mode_is_absent() -> None:
    # No mode published: the heuristic is the only signal, and it must say so.
    assert "heuristic" in reject("vendor/hd/1024-x-1792/dall-e-3")
    assert "heuristic" in reject("vendor/gpt-4o-mini-realtime-preview")
    # An explicit chat mode outranks an unlucky name.
    assert admit("vendor/image-describer-chat", mode="chat") == "vendor/image-describer-chat"


def test_missing_model_name_is_rejected() -> None:
    model_id, reason = classify(
        {"model_info": {}, "litellm_params": {}}, allow_wildcards=False, allow_other_protocols=False
    )
    assert model_id == ""
    assert "no model_name" in reason


# --- capacity is reported only when LiteLLM published it ----------------------


def test_capacity_uses_published_values() -> None:
    info = {"max_input_tokens": 1_000_000, "max_output_tokens": 128_000}
    assert capacity(info, {}) == (1_000_000, 128_000)


def test_capacity_falls_back_to_model_len_and_params() -> None:
    assert capacity({"max_model_len": 200_000}, {"max_output_tokens": 8_192}) == (200_000, 8_192)


def test_absent_capacity_is_none_not_a_fabricated_default() -> None:
    assert capacity({}, {}) == (None, None)


def test_boolean_is_not_a_capacity() -> None:
    assert capacity({"max_input_tokens": True, "max_output_tokens": False}, {}) == (None, None)


def test_non_positive_capacity_is_not_a_capacity() -> None:
    assert capacity({"max_input_tokens": 0, "max_output_tokens": -5}, {}) == (None, None)


def test_undetected_capacity_is_omitted_from_the_profile() -> None:
    models, _ = build_models([entry("vendor/unknown-limits")], allow_wildcards=False, allow_other_protocols=False)
    assert models == [{"id": "vendor/unknown-limits", "name": "vendor/unknown-limits"}]
    assert "contextWindow" not in models[0]
    assert "maxTokens" not in models[0]


def test_build_models_dedupes_and_sorts() -> None:
    models, rejected = build_models(
        [entry("vendor/b", mode="chat"), entry("vendor/a", mode="chat"), entry("vendor/b", mode="chat")],
        allow_wildcards=False,
        allow_other_protocols=False,
    )
    assert [model["id"] for model in models] == ["vendor/a", "vendor/b"]
    assert rejected.get("duplicate model_name") == 1


# --- the merge preserves everything it was not asked to change ---------------


PRIOR_SETTINGS = {
    "agent-default-model": {"provider": "deepseek-official", "model": "deepseek-flash", "reasoningEffort": "high"},
    "llm-pi-ai": {"providers": {"anthropic": {"displayName": "Anthropic", "models": []}}},
    "some-other-namespace": {"keep": "me"},
    "welcome-notice": {"acknowledged": True},
}


def test_merge_replaces_only_the_named_route() -> None:
    profile = build_provider(
        [{"id": "vendor/a", "name": "vendor/a"}],
        display_name="LiteLLM",
        api="openai-completions",
        base_url="http://127.0.0.1:4000",
        credential_ref="LITELLM_API_KEY",
    )
    merged = merge_provider(PRIOR_SETTINGS, "litellm", profile)

    assert merged["agent-default-model"] == PRIOR_SETTINGS["agent-default-model"]
    assert merged["some-other-namespace"] == {"keep": "me"}
    assert merged["welcome-notice"] == {"acknowledged": True}
    # A sibling provider route survives untouched.
    assert merged["llm-pi-ai"]["providers"]["anthropic"] == {"displayName": "Anthropic", "models": []}
    assert merged["llm-pi-ai"]["providers"]["litellm"] == profile


def test_merge_does_not_mutate_its_input() -> None:
    before = yaml.safe_dump(PRIOR_SETTINGS)
    merge_provider(PRIOR_SETTINGS, "litellm", {"models": []})
    assert yaml.safe_dump(PRIOR_SETTINGS) == before


def test_merge_creates_the_section_when_absent() -> None:
    merged = merge_provider({}, "litellm", {"models": []})
    assert merged == {"llm-pi-ai": {"providers": {"litellm": {"models": []}}}}


def test_merge_refuses_a_non_mapping_section() -> None:
    with pytest.raises(CatalogError, match="not a mapping"):
        merge_provider({"llm-pi-ai": ["not", "a", "mapping"]}, "litellm", {"models": []})


def test_base_url_gains_the_v1_suffix_exactly_once() -> None:
    profile = build_provider([], display_name="L", api="openai-completions", base_url="http://h:4000/", credential_ref="K")
    assert profile["baseURL"] == "http://h:4000/v1"


# --- fail closed on documents we cannot trust --------------------------------


def test_malformed_yaml_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("llm-pi-ai:\n  providers: [unterminated\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="not valid YAML"):
        load_yaml_strict(str(path), must_exist=False)


def test_non_mapping_yaml_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="expected a mapping"):
        load_yaml_strict(str(path), must_exist=False)


def test_empty_document_is_an_empty_mapping(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("", encoding="utf-8")
    assert load_yaml_strict(str(path), must_exist=False) == {}


def test_missing_file_fails_closed_only_when_required(tmp_path: Path) -> None:
    path = str(tmp_path / "absent.yaml")
    assert load_yaml_strict(path, must_exist=False) == {}
    with pytest.raises(CatalogError, match="does not exist"):
        load_yaml_strict(path, must_exist=True)


def test_write_atomic_is_private_and_backs_up(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("original: true\n", encoding="utf-8")
    os.chmod(path, 0o644)

    backup = write_atomic(str(path), "replaced: true\n", backup=True)

    assert backup is not None and Path(backup).read_text(encoding="utf-8") == "original: true\n"
    assert path.read_text(encoding="utf-8") == "replaced: true\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(backup).stat().st_mode) == 0o600


def test_write_atomic_leaves_the_target_unchanged_on_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("original: true\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        write_atomic(str(path), "broken: [unterminated\n", backup=True)

    assert path.read_text(encoding="utf-8") == "original: true\n"
    # No temp file and no backup left behind by the failed attempt.
    assert [p.name for p in tmp_path.iterdir()] == ["settings.yaml"]


def test_no_temporary_files_survive_a_successful_write(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    write_atomic(str(path), render_yaml({"a": 1}), backup=False)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.yaml"]


# --- the importer must never touch the credential store ----------------------


def test_source_never_references_the_credential_store() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert ".credentials.yaml" not in source
    assert "browser-session" not in source
    assert "credentialKey" not in source


def test_report_accounts_for_every_entry() -> None:
    models, rejected = build_models(
        [
            entry("vendor/chat-a", mode="chat"),
            entry("vendor/embed-b", mode="embedding"),
            entry("vendor/wild-c", wildcard=True),
        ],
        allow_wildcards=False,
        allow_other_protocols=False,
    )
    report = format_report(3, models, rejected)
    assert "admitted as chat-completions models: 1" in report
    assert sum(rejected.values()) == 2
    # Undetected capacity is disclosed as inheritance, never as measurement —
    # and each field's gap is reported separately.
    assert "contextWindow inherited from the DSH route default" in report
    assert "maxTokens inherited from the DSH route default" in report


def test_report_separates_the_two_capacity_gaps() -> None:
    models, _ = build_models(
        [
            entry("vendor/full", mode="chat", max_input_tokens=200_000, max_output_tokens=8_192),
            entry("vendor/context-only", mode="chat", max_input_tokens=128_000),
            entry("vendor/neither", mode="chat"),
        ],
        allow_wildcards=False,
        allow_other_protocols=False,
    )
    report = format_report(3, models, {})
    assert "with detected contextWindow: 2" in report
    assert "with detected maxTokens:     1" in report
    assert "contextWindow inherited from the DSH route default (not published by LiteLLM): 1" in report
    assert "maxTokens inherited from the DSH route default (not published by LiteLLM):     2" in report


def test_main_refuses_to_write_an_empty_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text("agent-default-model: {provider: p, model: m}\n", encoding="utf-8")
    # `runpy.run_path` returns a copy of the module globals, so patch the dict
    # `main` actually resolves through rather than the returned copy.
    monkeypatch.setitem(
        catalog["main"].__globals__,
        "fetch_model_info",
        lambda *a, **k: [entry("vendor/embed", mode="embedding")],
    )

    assert catalog["main"]([
        "--settings", str(settings), "--apply",
    ]) == 1
    assert settings.read_text(encoding="utf-8") == "agent-default-model: {provider: p, model: m}\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.yaml"]
