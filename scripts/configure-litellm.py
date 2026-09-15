#!/usr/bin/env python3
"""Configure LiteLLM models in DeepSeek Harness settings.yaml and .credentials.yaml."""

import json
import os
import sys
import urllib.request
import yaml

PROD_DATA = "/srv/deepseek-harness/data"
SETTINGS_PATH = os.path.join(PROD_DATA, "settings.yaml")
CREDENTIALS_PATH = os.path.join(PROD_DATA, ".credentials.yaml")
LITELLM_MODELS_URL = "http://127.0.0.1:4000/models"
LITELLM_BASE_URL = "http://127.0.0.1:4000/v1"

print(f"Fetching models from {LITELLM_MODELS_URL}...")
try:
    req = urllib.request.Request(LITELLM_MODELS_URL, headers={"Authorization": "Bearer sk-dummy"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
except Exception as e:
    print(f"ERROR: Failed to query LiteLLM models: {e}", file=sys.stderr)
    sys.exit(1)

raw_models = data.get("data", [])
print(f"Discovered {len(raw_models)} total models from LiteLLM.")

chat_models = []
for m in raw_models:
    mid = m.get("id", "")
    mid_lower = mid.lower()
    # Filter out embedding, reranking, and transcription-only models
    if any(term in mid_lower for term in ["embedding", "embed", "rerank", "whisper", "tts"]):
        continue

    model_entry = {
        "id": mid,
        "name": mid,
    }

    # Extract context window / max tokens if available
    context_window = m.get("max_input_tokens") or m.get("context_window") or m.get("context_length")
    if isinstance(context_window, int) and context_window > 0:
        model_entry["contextWindow"] = context_window

    max_tokens = m.get("max_output_tokens") or m.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        model_entry["maxTokens"] = max_tokens

    chat_models.append(model_entry)

print(f"Filtered to {len(chat_models)} chat-compatible models.")

# 1. Update .credentials.yaml with LITELLM_API_KEY and invalidate old browser session
creds_data = {"version": 1, "refs": {}, "records": {}}
if os.path.exists(CREDENTIALS_PATH):
    try:
        with open(CREDENTIALS_PATH, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                creds_data = loaded
    except Exception as e:
        print(f"Warning: Could not parse existing credentials: {e}", file=sys.stderr)

creds_data.setdefault("version", 1)
creds_data.setdefault("refs", {})
creds_data.setdefault("records", {})
creds_data["refs"]["LITELLM_API_KEY"] = "sk-litellm-local"

# Invalidate previous browser session secret per advisor guidance
if "client-connection/browser-session" in creds_data["records"]:
    print("Invalidating previous browser-session secret...")
    del creds_data["records"]["client-connection/browser-session"]

tmp_creds = CREDENTIALS_PATH + ".tmp"
with open(tmp_creds, "w", encoding="utf-8") as f:
    yaml.dump(creds_data, f, default_flow_style=False, sort_keys=False)
os.chmod(tmp_creds, 0o600)
os.replace(tmp_creds, CREDENTIALS_PATH)
print(f"Updated {CREDENTIALS_PATH}.")

# 2. Update settings.yaml with llm-pi-ai.providers.litellm
settings_data = {}
if os.path.exists(SETTINGS_PATH):
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                settings_data = loaded
    except Exception as e:
        print(f"Warning: Could not parse existing settings: {e}", file=sys.stderr)

settings_data.setdefault("llm-pi-ai", {})
settings_data["llm-pi-ai"].setdefault("providers", {})

settings_data["llm-pi-ai"]["providers"]["litellm"] = {
    "displayName": "LiteLLM",
    "api": "openai-completions",
    "baseURL": LITELLM_BASE_URL,
    "apiKeyEnv": "LITELLM_API_KEY",
    "models": chat_models,
}

tmp_settings = SETTINGS_PATH + ".tmp"
with open(tmp_settings, "w", encoding="utf-8") as f:
    yaml.dump(settings_data, f, default_flow_style=False, sort_keys=False)
os.chmod(tmp_settings, 0o600)
os.replace(tmp_settings, SETTINGS_PATH)
print(f"Updated {SETTINGS_PATH} with {len(chat_models)} LiteLLM models.")
print("LiteLLM configuration complete.")
