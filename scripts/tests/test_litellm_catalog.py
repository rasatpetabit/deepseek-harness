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
reasoning_efforts = catalog["reasoning_efforts"]
declared_model_ids = catalog["declared_model_ids"]
restrict_to_existing = catalog["restrict_to_existing"]
build_models = catalog["build_models"]
build_provider = catalog["build_provider"]
merge_provider = catalog["merge_provider"]
load_yaml_strict = catalog["load_yaml_strict"]
write_atomic = catalog["write_atomic"]
load_lock = catalog["load_lock"]
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


# --- the lock file sits beside the credential store, so it is private ---------


def test_lock_file_is_created_private_and_cleaned_on_release(tmp_path: Path) -> None:
    lock_path = str(tmp_path / "settings.yaml.lock")
    lock = load_lock(lock_path)
    try:
        assert lock is not None
        assert stat.S_IMODE(os.stat(lock_path).st_mode) == 0o600
    finally:
        if lock is not None:
            lock.release()
    assert not os.path.exists(lock_path)


def test_lock_file_mode_is_normalized_even_if_it_already_existed(tmp_path: Path) -> None:
    lock_path = tmp_path / "settings.yaml.lock"
    lock_path.write_text("", encoding="utf-8")
    os.chmod(lock_path, 0o644)
    lock = load_lock(str(lock_path))
    try:
        assert stat.S_IMODE(os.stat(lock_path).st_mode) == 0o600
    finally:
        if lock is not None:
            lock.release()
    assert not lock_path.exists()


def test_a_second_concurrent_run_fails_closed(tmp_path: Path) -> None:
    lock_path = str(tmp_path / "settings.yaml.lock")
    first = load_lock(lock_path)
    assert first is not None
    try:
        with pytest.raises(CatalogError, match="refusing to race it"):
            load_lock(lock_path)
    finally:
        first.release()
    # Released, the lock is acquirable again.
    second = load_lock(lock_path)
    assert second is not None
    second.release()


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


# --- reasoning efforts are transcribed, never guessed -------------------------


def test_published_map_is_transcribed_in_escalation_order() -> None:
    info = {"thinking_level_map": {"high": "high", "low": "low", "medium": "medium"}}
    assert reasoning_efforts(info) == {"low": "low", "medium": "medium", "high": "high"}


def test_wire_spelling_differs_from_level_name() -> None:
    # The value is what dispatch sends, so it need not match the key.
    assert reasoning_efforts({"thinking_level_map": {"max": "ultra"}}) == {"max": "ultra"}


def test_valueless_off_is_kept_because_dsh_allows_it() -> None:
    assert reasoning_efforts({"thinking_level_map": {"off": None, "low": "low"}}) == {
        "off": None,
        "low": "low",
    }


def test_valueless_level_other_than_off_is_dropped() -> None:
    # DSH rejects a null wire value on any level but off, so forwarding it
    # would fail the whole route rather than describe the model.
    assert reasoning_efforts({"thinking_level_map": {"high": None, "low": "low"}}) == {"low": "low"}


def test_empty_wire_string_is_dropped() -> None:
    assert reasoning_efforts({"thinking_level_map": {"low": "", "high": "high"}}) == {"high": "high"}


def test_non_string_wire_value_is_dropped() -> None:
    assert reasoning_efforts({"thinking_level_map": {"low": 3, "high": "high"}}) == {"high": "high"}


def test_levels_outside_the_dsh_vocabulary_are_dropped() -> None:
    assert reasoning_efforts({"thinking_level_map": {"turbo": "turbo", "low": "low"}}) == {"low": "low"}


@pytest.mark.parametrize("published", [
    None,
    {},
    "low,high",
    {"off": None},
    {"high": None, "max": None},
    {"turbo": "turbo"},
])
def test_nothing_declarable_means_absent_not_a_rejected_claim(published: Any) -> None:
    # Absent lets DSH inherit its catalog default. Emitting an empty dict, an
    # off-only dict, or an unknown level would be refused by the schema.
    assert reasoning_efforts({"thinking_level_map": published}) is None


def test_missing_map_leaves_the_field_absent() -> None:
    assert reasoning_efforts({}) is None


def test_build_models_attaches_transcribed_efforts_only() -> None:
    models, _ = build_models(
        [
            entry("vendor/thinks", mode="chat", thinking_level_map={"low": "low", "high": "high"}),
            entry("vendor/plain", mode="chat"),
        ],
        allow_wildcards=False,
        allow_other_protocols=False,
    )
    by_id = {m["id"]: m for m in models}
    assert by_id["vendor/thinks"]["reasoningEfforts"] == {"low": "low", "high": "high"}
    assert "reasoningEfforts" not in by_id["vendor/plain"]


def test_report_counts_transcribed_efforts(monkeypatch: pytest.MonkeyPatch) -> None:
    report = format_report(
        2,
        [{"id": "a", "reasoningEfforts": {"low": "low"}}, {"id": "b"}],
        {},
    )
    assert "with transcribed reasoningEfforts: 1" in report


# --- a capability refresh does not silently change the offered set -------------


def declared_settings(*model_ids: str) -> dict[str, Any]:
    return {
        "llm-pi-ai": {
            "providers": {
                "litellm": {"models": [{"id": mid, "name": mid} for mid in model_ids]}
            }
        }
    }


def test_declared_model_ids_reads_the_route() -> None:
    assert declared_model_ids(declared_settings("a", "b/c"), "litellm") == {"a", "b/c"}


@pytest.mark.parametrize("document", [
    {},
    {"llm-pi-ai": None},
    {"llm-pi-ai": "nope"},
    {"llm-pi-ai": {"providers": None}},
    {"llm-pi-ai": {"providers": {"litellm": None}}},
    {"llm-pi-ai": {"providers": {"litellm": {"models": None}}}},
    {"llm-pi-ai": {"providers": {"other": {}}}},
    {"llm-pi-ai": {"providers": {"litellm": {"models": [{"name": "no id"}]}}}},
])
def test_declared_model_ids_is_empty_rather_than_raising(document: Any) -> None:
    assert declared_model_ids(document, "litellm") == set()


def test_restrict_to_existing_counts_what_it_held_back() -> None:
    models = [{"id": "keep"}, {"id": "new"}, {"id": "also-new"}]
    retained, held = restrict_to_existing(models, {"keep", "absent"})
    assert [m["id"] for m in retained] == ["keep"]
    assert held == 2


def test_existing_only_preserves_the_declared_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump(declared_settings("vendor/keep", "vendor/gone")), encoding="utf-8"
    )
    monkeypatch.setitem(
        catalog["main"].__globals__,
        "fetch_model_info",
        lambda *a, **k: [
            entry("vendor/keep", mode="chat", thinking_level_map={"low": "low"}),
            entry("vendor/new", mode="chat", thinking_level_map={"low": "low"}),
        ],
    )

    assert catalog["main"]([
        "--settings", str(settings), "--apply", "--existing-only",
    ]) == 0

    written = yaml.safe_load(settings.read_text(encoding="utf-8"))
    models = written["llm-pi-ai"]["providers"]["litellm"]["models"]
    # `vendor/gone` is no longer published, `vendor/new` was never offered here.
    assert [m["id"] for m in models] == ["vendor/keep"]
    assert models[0]["reasoningEfforts"] == {"low": "low"}


def test_existing_only_refuses_when_nothing_is_declared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text("agent-default-model: {provider: p, model: m}\n", encoding="utf-8")
    monkeypatch.setitem(
        catalog["main"].__globals__,
        "fetch_model_info",
        lambda *a, **k: [entry("vendor/new", mode="chat")],
    )

    assert catalog["main"]([
        "--settings", str(settings), "--apply", "--existing-only",
    ]) == 1
    # Nothing written: there was no set to preserve, and inventing one is not
    # what the flag promised.
    assert settings.read_text(encoding="utf-8") == "agent-default-model: {provider: p, model: m}\n"


def test_existing_only_refuses_when_no_declared_model_survives_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text(yaml.safe_dump(declared_settings("vendor/retired")), encoding="utf-8")
    monkeypatch.setitem(
        catalog["main"].__globals__,
        "fetch_model_info",
        lambda *a, **k: [entry("vendor/new", mode="chat")],
    )

    assert catalog["main"]([
        "--settings", str(settings), "--apply", "--existing-only",
    ]) == 1
    written = yaml.safe_load(settings.read_text(encoding="utf-8"))
    assert [m["id"] for m in written["llm-pi-ai"]["providers"]["litellm"]["models"]] == [
        "vendor/retired"
    ]


def test_report_names_held_back_models() -> None:
    assert "held back by --existing-only" in format_report(3, [{"id": "a"}], {}, 2)
    assert "held back" not in format_report(3, [{"id": "a"}], {})
