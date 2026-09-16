#!/usr/bin/env python3
"""Discover LiteLLM chat models and emit a `llm-pi-ai` provider profile for DSH.

Discovery-only by default. Nothing is written unless `--apply` is passed, and
this script never reads or writes the credential store: an `apiKeyEnv` is a
*reference*, and supplying the secret is the operator's act, not a side effect
of listing models.

Classification is taken from LiteLLM's authoritative `/model/info` capability
metadata rather than from model-name substrings. Every exclusion is reported
with its reason, and every capacity value is emitted only when LiteLLM actually
published it — a model with no published capacity is emitted without
`contextWindow`/`maxTokens` so that DSH's route defaults apply visibly instead
of a fabricated number being recorded as if it were measured.

Run the tests with:

    python3 -m pytest scripts/tests/test_litellm_catalog.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

try:
    import yaml
except ImportError:  # pragma: no cover
    print("error: PyYAML is required (python3 -m pip install pyyaml)", file=sys.stderr)
    raise SystemExit(2)

DEFAULT_BASE_URL = "http://127.0.0.1:4000"
DEFAULT_PROVIDER = "litellm"
DEFAULT_DISPLAY_NAME = "LiteLLM"
DEFAULT_API = "openai-completions"
DEFAULT_CREDENTIAL_REF = "LITELLM_API_KEY"

# LiteLLM `mode` values that are not chat-completions endpoints. A model in any
# of these will fail under `api: openai-completions`, so it is never offered.
NON_CHAT_MODES = frozenset({
    "embedding",
    "embeddings",
    "rerank",
    "image_generation",
    "image_edit",
    "image",
    "audio_transcription",
    "transcription",
    "tts",
    "speech",
    "moderation",
    "guard",
    "guardrails",
    "ocr",
    "video_generation",
})

# Modes that speak a different wire protocol than chat completions. Excluded by
# default and reported, because admitting them needs a separate DSH route with
# its own `api`, not a silent entry in a chat-completions list.
OTHER_PROTOCOL_MODES = frozenset({"responses"})

# LiteLLM publishes a model's selectable reasoning levels and their wire
# spellings under this `model_info` key. It is the authoritative source for what
# a model accepts: DSH's own schema docs warn that a hand-declared effort list is
# "a claim about the endpoint, not a check of it", so this importer only ever
# transcribes what the proxy already published and never guesses levels.
REASONING_MAP_FIELD = "thinking_level_map"

# The levels DSH's `llm-pi-ai` schema accepts as `reasoningEfforts` keys, in
# escalation order. This mirrors `THINKING_LEVEL_GATE` in
# packages/llm/llm-pi-ai/src/catalog.ts, which is the authority: that package's
# zod union rejects any other key, so a level absent there cannot be declared
# here. Literal because it is a cross-language schema contract rather than an
# observed value - it changes only when DSH's own vocabulary changes.
DSH_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

# DSH permits a valueless wire spelling for this level alone, meaning "supported,
# send nothing"; every other level must name the value to dispatch.
VALUELESS_LEVEL = "off"

# The settings document's address for a provider route. One spelling, used by
# both the merge and the preservation check, so the two cannot drift apart.
SETTINGS_SECTION = "llm-pi-ai"
PROVIDERS_KEY = "providers"

# Heuristic name filter, applied ONLY when LiteLLM published no `mode`. This is
# a guess, and the report says so: it is never presented as capability metadata.
NON_CHAT_NAME = re.compile(
    r"(?:^|[/.-])(?:dall-e|sora|flux|imagen|midjourney|stable-diffusion)"
    r"|(?:^|[/.-])(?:embed|embedding|rerank|reranker|whisper|tts|stt|asr|ocr)"
    r"|realtime|transcri|moderation|guardrail"
    r"|(?:^|/)\d+x\d+(?:/|$)"
    r"|(?:^|[/.-])(?:image|images|video|audio|speech|voice)(?:[/.-]|$)",
    re.IGNORECASE,
)


class CatalogError(RuntimeError):
    """A discovery or configuration failure that must stop the run."""


def fetch_model_info(base_url: str, timeout: float, api_key: str | None) -> list[dict[str, Any]]:
    """Read LiteLLM's authoritative per-model capability metadata."""
    url = base_url.rstrip("/") + "/model/info"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise CatalogError(f"{url} returned HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise CatalogError(f"cannot reach {url}: {error.reason}") from error
    except json.JSONDecodeError as error:
        raise CatalogError(f"{url} did not return JSON: {error}") from error

    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise CatalogError(f"{url} has no 'data' array; refusing to guess a catalog")
    return [entry for entry in entries if isinstance(entry, dict)]


def positive_int(value: Any) -> int | None:
    """A capacity LiteLLM actually published, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if number > 0 else None


def capacity(model_info: dict[str, Any], litellm_params: dict[str, Any]) -> tuple[int | None, int | None]:
    """Detected (contextWindow, maxTokens). Absent means genuinely undetected."""
    context = (
        positive_int(model_info.get("max_input_tokens"))
        or positive_int(model_info.get("max_model_len"))
        or positive_int(litellm_params.get("max_input_tokens"))
    )
    max_tokens = (
        positive_int(model_info.get("max_output_tokens"))
        or positive_int(model_info.get("max_tokens"))
        or positive_int(litellm_params.get("max_output_tokens"))
    )
    return context, max_tokens


def reasoning_efforts(model_info: dict[str, Any]) -> dict[str, Any] | bool | None:
    """Transcribe LiteLLM's published level map into a DSH `reasoningEfforts` value.

    Three outcomes, and telling them apart is the point:

    * a dict of levels, when the proxy published at least one thinking level;
    * `False`, when the proxy published a map offering nothing beyond `off`. That
      is a positive claim the model does not reason. Omitting the field instead
      would leave DSH inheriting the installed catalog entry's capability, which
      can advertise reasoning this endpoint does not serve;
    * None, when the proxy published no usable map at all. Absent means "not
      stated here", so DSH inherits its catalog default rather than a guess.

    Levels the proxy leaves valueless are dropped unless they are `off`, the only
    level DSH permits valueless. Keys outside DSH's vocabulary are dropped rather
    than forwarded, because the schema union does not accept them.

    Wire *values* are passed through as published and are not validated against
    what any upstream accepts - nothing here could check that, and neither does
    the consumer, which rejects only a null value on a level other than `off` and
    an empty string. A spelling the provider does not honour therefore passes
    config load and fails mid-turn at the provider.
    """
    published = model_info.get(REASONING_MAP_FIELD)
    if not isinstance(published, dict) or not published:
        return None
    efforts: dict[str, Any] = {}
    for level in DSH_THINKING_LEVELS:
        if level not in published:
            continue
        wire = published[level]
        if wire is None:
            if level == VALUELESS_LEVEL:
                efforts[level] = None
        elif isinstance(wire, str) and wire:
            efforts[level] = wire
    if not any(level != VALUELESS_LEVEL for level in efforts):
        return False
    return efforts


def classify(entry: dict[str, Any], *, allow_wildcards: bool, allow_other_protocols: bool) -> tuple[str | None, str]:
    """Decide one entry's fate.

    @returns (model_id, "") to admit, or ("", reason) to reject.
    """
    model_id = entry.get("model_name")
    if not isinstance(model_id, str) or not model_id.strip():
        return "", "no model_name"
    model_id = model_id.strip()

    model_info = entry.get("model_info") if isinstance(entry.get("model_info"), dict) else {}
    litellm_params = entry.get("litellm_params") if isinstance(entry.get("litellm_params"), dict) else {}

    if "*" in model_id:
        return "", "wildcard pattern in model_name"
    if model_info.get("wildcard") and not allow_wildcards:
        # A catch-all route placeholder: LiteLLM synthesizes it with the route's
        # generic defaults, which are not this model's measured capacity.
        return "", "wildcard route placeholder (not concretely configured)"

    mode = model_info.get("mode")
    if isinstance(mode, str) and mode.strip():
        mode = mode.strip().lower()
        if mode in NON_CHAT_MODES:
            return "", f"mode={mode} is not chat completions"
        if mode in OTHER_PROTOCOL_MODES:
            if not allow_other_protocols:
                return "", f"mode={mode} needs its own api route"
            return model_id, ""
        return model_id, ""

    # No published mode: fall back to the name heuristic and say so.
    if NON_CHAT_NAME.search(model_id):
        return "", "no mode published; name matches a non-chat pattern (heuristic)"
    return model_id, ""


def build_models(entries: list[dict[str, Any]], *, allow_wildcards: bool, allow_other_protocols: bool) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Admitted DSH model profiles, plus rejection counts by reason."""
    models: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    seen: set[str] = set()

    for entry in entries:
        model_id, reason = classify(
            entry,
            allow_wildcards=allow_wildcards,
            allow_other_protocols=allow_other_protocols,
        )
        if not model_id:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        if model_id in seen:
            rejected["duplicate model_name"] = rejected.get("duplicate model_name", 0) + 1
            continue
        seen.add(model_id)

        model_info = entry.get("model_info") if isinstance(entry.get("model_info"), dict) else {}
        litellm_params = entry.get("litellm_params") if isinstance(entry.get("litellm_params"), dict) else {}
        context, max_tokens = capacity(model_info, litellm_params)

        profile: dict[str, Any] = {"id": model_id, "name": model_id}
        if context is not None:
            profile["contextWindow"] = context
        if max_tokens is not None:
            profile["maxTokens"] = max_tokens
        efforts = reasoning_efforts(model_info)
        if efforts is not None:
            profile["reasoningEfforts"] = efforts
        models.append(profile)

    models.sort(key=lambda item: item["id"])
    return models, rejected


def build_provider(models: list[dict[str, Any]], *, display_name: str, api: str, base_url: str, credential_ref: str) -> dict[str, Any]:
    """The `llm-pi-ai.providers.<route>` profile document."""
    return {
        "displayName": display_name,
        "api": api,
        "baseURL": base_url.rstrip("/") + "/v1",
        "apiKeyEnv": credential_ref,
        "models": models,
    }


def load_yaml_strict(path: str, *, must_exist: bool) -> dict[str, Any]:
    """Parse a YAML mapping, failing closed on anything unexpected."""
    if not os.path.exists(path):
        if must_exist:
            raise CatalogError(f"{path} does not exist")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except OSError as error:
        raise CatalogError(f"cannot read {path}: {error}") from error
    except yaml.YAMLError as error:
        # Never warn-and-continue here: rewriting a document we could not parse
        # is how unrelated configuration gets destroyed.
        raise CatalogError(f"refusing to continue: {path} is not valid YAML: {error}") from error
    if document is None:
        return {}
    if not isinstance(document, dict):
        raise CatalogError(f"refusing to continue: {path} is a {type(document).__name__}, expected a mapping")
    return document


def merge_provider(settings: dict[str, Any], provider: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Replace exactly one provider route; every other key is preserved."""
    merged = dict(settings)
    section = merged.get(SETTINGS_SECTION)
    if section is None:
        section = {}
    elif not isinstance(section, dict):
        raise CatalogError(f"refusing to continue: settings '{SETTINGS_SECTION}' is not a mapping")
    else:
        section = dict(section)

    providers = section.get(PROVIDERS_KEY)
    if providers is None:
        providers = {}
    elif not isinstance(providers, dict):
        raise CatalogError(
            f"refusing to continue: settings '{SETTINGS_SECTION}.{PROVIDERS_KEY}' is not a mapping"
        )
    else:
        providers = dict(providers)

    providers[provider] = profile
    section[PROVIDERS_KEY] = providers
    merged[SETTINGS_SECTION] = section
    return merged


def declared_model_ids(settings: dict[str, Any], provider: str) -> set[str]:
    """The model ids a settings document already declares for one route.

    An absent section, providers mapping, route, or models list yields the empty
    set, which the caller treats as "nothing to preserve" and refuses on. A
    *malformed* one is an error instead: silently contributing nothing for a
    declared model would narrow the preserved set by exactly that model, and the
    refresh would then drop it from the route while reporting success.
    """
    section = settings.get(SETTINGS_SECTION)
    if section is None:
        return set()
    if not isinstance(section, dict):
        raise CatalogError(f"refusing to continue: settings '{SETTINGS_SECTION}' is not a mapping")
    providers = section.get(PROVIDERS_KEY)
    if providers is None:
        return set()
    if not isinstance(providers, dict):
        raise CatalogError(
            f"refusing to continue: settings '{SETTINGS_SECTION}.{PROVIDERS_KEY}' is not a mapping"
        )
    route = providers.get(provider)
    if route is None:
        return set()
    if not isinstance(route, dict):
        raise CatalogError(f"refusing to continue: provider route '{provider}' is not a mapping")
    models = route.get("models")
    if models is None:
        return set()
    if not isinstance(models, list):
        raise CatalogError(f"refusing to continue: provider route '{provider}' models is not a list")
    declared: set[str] = set()
    for index, model in enumerate(models):
        model_id = model.get("id") if isinstance(model, dict) else None
        if not isinstance(model_id, str) or not model_id.strip():
            raise CatalogError(
                f"refusing to continue: provider route '{provider}' models[{index}] has no usable"
                " id; preserving the declared set needs every entry identifiable"
            )
        declared.add(model_id)
    return declared


def restrict_to_existing(models: list[dict[str, Any]], declared: set[str]) -> tuple[list[dict[str, Any]], int]:
    """Hold the emitted set to models already declared. @returns (retained, held back).

    A capability refresh and a catalog refresh are different acts. Discovery
    admits whatever the proxy now publishes concretely, and that set grows as
    routing variants are added upstream of this tool. Preserving the declared set
    keeps a re-declaration of capability from also changing what the picker
    offers, which is an act to choose deliberately rather than inherit.

    Order follows discovery, which sorts by id, so a refresh can reorder the
    served list even when the set is unchanged. That is churn in the document,
    not a change in what is offered.
    """
    retained = [model for model in models if model["id"] in declared]
    return retained, len(models) - len(retained)


def render_yaml(document: dict[str, Any]) -> str:
    return yaml.safe_dump(document, default_flow_style=False, sort_keys=False, allow_unicode=True, width=100)


def write_atomic(path: str, text: str, *, backup: bool) -> str | None:
    """Write through a private temp file in the target directory, then rename.

    @returns the backup path when one was taken, else None.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."

    # Parse before touching anything: a document that will not round-trip must
    # neither reach the live path nor leave a stray backup beside it.
    yaml.safe_load(text)

    backup_path: str | None = None
    if backup and os.path.exists(path):
        backup_path = path + ".bak"
        with open(path, "rb") as source, open(backup_path, "wb") as sink:
            sink.write(source.read())
        os.chmod(backup_path, 0o600)

    handle, temporary = tempfile.mkstemp(prefix=".litellm-catalog-", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return backup_path


class FileLock:
    """Advisory lock that cleans up its lock file on release.

    DSH's internal withFileLock (dsh-atomic-write) uses `writeFile(..., { flag: 'wx' })`
    on `<filename>.lock`, which fails if the lock file already exists. Cleaning up
    the lock file on release ensures subsequent settings writes are never blocked.
    """

    def __init__(self, path: str, descriptor: int) -> None:
        self.path = path
        self.descriptor = descriptor

    def release(self) -> None:
        try:
            if fcntl is not None:
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self.descriptor)
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def __enter__(self) -> "FileLock":
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


def load_lock(path: str) -> FileLock | None:
    """An advisory lock so two concurrent runs cannot interleave writes."""
    if fcntl is None:
        return None
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    except BaseException:
        os.close(descriptor)
        raise
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        raise CatalogError(f"another litellm-catalog run holds {path}; refusing to race it") from None
    return FileLock(path, descriptor)


def discover(base_url: str, *, timeout: float, api_key: str | None, allow_wildcards: bool, allow_other_protocols: bool) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    """Fetch and classify. @returns (models, rejection counts, total entries)."""
    entries = fetch_model_info(base_url, timeout, api_key)
    models, rejected = build_models(
        entries,
        allow_wildcards=allow_wildcards,
        allow_other_protocols=allow_other_protocols,
    )
    return models, rejected, len(entries)


def format_report(total: int, models: list[dict[str, Any]], rejected: dict[str, int], held_back: int = 0) -> str:
    with_context = sum(1 for model in models if "contextWindow" in model)
    with_output = sum(1 for model in models if "maxTokens" in model)
    with_efforts = sum(1 for model in models if "reasoningEfforts" in model)
    lines = [
        f"LiteLLM model_info entries: {total}",
        f"admitted as chat-completions models: {len(models)}",
        f"  with detected contextWindow: {with_context}",
        f"  with detected maxTokens:     {with_output}",
        f"  with transcribed reasoningEfforts: {with_efforts}",
        f"  contextWindow inherited from the DSH route default (not published by LiteLLM): {len(models) - with_context}",
        f"  maxTokens inherited from the DSH route default (not published by LiteLLM):     {len(models) - with_output}",
    ]
    if held_back:
        lines.append(f"held back by --existing-only (declared model set preserved): {held_back}")
    if rejected:
        lines.append("rejected:")
        for reason, count in sorted(rejected.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"  {count:>5}  {reason}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("LITELLM_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--provider", default=DEFAULT_PROVIDER, help="the providers dict key, which IS the route")
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--credential-ref", default=DEFAULT_CREDENTIAL_REF, help="apiKeyEnv reference name; the secret is never handled here")
    parser.add_argument("--api-key", default=os.environ.get("LITELLM_MASTER_KEY"), help="LiteLLM master key, if the proxy enforces one")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--allow-wildcards", action="store_true", help="admit catch-all route placeholders (their capacities are route defaults, not measured)")
    parser.add_argument("--allow-other-protocols", action="store_true", help="admit `responses`-mode models into a chat-completions route")
    parser.add_argument("--existing-only", action="store_true", help="emit only models --settings already declares for this route: refresh capability without changing the offered set")
    parser.add_argument("--allow-missing", action="store_true", help="with --existing-only, drop declared models discovery no longer returns instead of refusing; a deliberate narrowing of the offered set")
    parser.add_argument("--settings", default="/srv/deepseek-harness/data/settings.yaml")
    parser.add_argument("--out", help="write the provider profile alone to this path instead of merging")
    parser.add_argument("--apply", action="store_true", help="merge into --settings (default: discovery only, write nothing)")
    parser.add_argument("--no-backup", action="store_true", help="skip the .bak copy when applying")
    parser.add_argument("--json", action="store_true", help="print the machine-readable profile instead of the report")
    args = parser.parse_args(argv)

    try:
        models, rejected, total = discover(
            args.base_url,
            timeout=args.timeout,
            api_key=args.api_key,
            allow_wildcards=args.allow_wildcards,
            allow_other_protocols=args.allow_other_protocols,
        )
        if not models:
            raise CatalogError("discovery admitted no models; refusing to write an empty provider route")

        held_back = 0
        if args.existing_only:
            declared = declared_model_ids(
                load_yaml_strict(args.settings, must_exist=False), args.provider
            )
            if not declared:
                raise CatalogError(
                    f"--existing-only needs models already declared under"
                    f" {SETTINGS_SECTION}.{PROVIDERS_KEY}.{args.provider} in {args.settings};"
                    " found none, so there is no set to preserve"
                )
            models, held_back = restrict_to_existing(models, declared)
            # Intersecting is not preserving. A partial discovery - a proxy
            # outage, a renamed upstream id, or a declared wildcard entry this
            # run was not told to admit - would write a route offering fewer
            # models than it was asked to keep, and report success. Refuse and
            # name what is missing; dropping a model is the operator's call.
            missing = sorted(declared - {model["id"] for model in models})
            if missing and not args.allow_missing:
                raise CatalogError(
                    "discovery did not return every declared model, so preserving the set would"
                    " silently narrow it; missing: " + ", ".join(missing)
                    + ". Re-run with --allow-missing to drop them deliberately."
                )
            if not models:
                raise CatalogError(
                    "none of the declared models were admitted by discovery; refusing to write"
                    " an empty provider route"
                )

        profile = build_provider(
            models,
            display_name=args.display_name,
            api=args.api,
            base_url=args.base_url,
            credential_ref=args.credential_ref,
        )

        if args.json:
            # The machine-readable path keeps stdout pure JSON, so the narrowing
            # signal goes to stderr rather than being dropped: a consumer of
            # this path must still be able to tell a filtered profile from a
            # complete one.
            if held_back:
                print(
                    f"held back by --existing-only (declared model set preserved): {held_back}",
                    file=sys.stderr,
                )
            print(json.dumps({args.provider: profile}, indent=2, sort_keys=False))
            return 0

        print(format_report(total, models, rejected, held_back))

        if args.out:
            text = render_yaml({args.provider: profile})
            backup = write_atomic(args.out, text, backup=not args.no_backup)
            print(f"\nwrote provider profile to {args.out}" + (f" (backup: {backup})" if backup else ""))
            return 0

        if not args.apply:
            print("\ndiscovery only: nothing was written. Re-run with --apply to merge into "
                  f"{args.settings}, or --out <path> to capture the profile alone.")
            print(f"The route will name apiKeyEnv '{args.credential_ref}'; supply that secret "
                  "yourself through DSH's credential store or environment. This script never "
                  "reads or writes credentials.")
            return 0

        lock = load_lock(args.settings + ".lock")
        try:
            settings = load_yaml_strict(args.settings, must_exist=False)
            merged = merge_provider(settings, args.provider, profile)
            backup = write_atomic(args.settings, render_yaml(merged), backup=not args.no_backup)
        finally:
            if lock is not None:
                lock.release()
        print(f"\nmerged provider '{args.provider}' into {args.settings}"
              + (f" (backup: {backup})" if backup else ""))
        print("Restart the harness to pick the document up.")
        return 0
    except CatalogError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
