"""
Multi-provider LLM calls behind one function.

Every provider is reached over plain HTTP so adding one is a dict entry rather
than a dependency. complete(prompt) returns the model's text; the caller does
the JSON parsing.
"""

import json

import requests

import config

TIMEOUT = 300


class ProviderError(RuntimeError):
    pass


def _post(url, headers, payload):
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise ProviderError(f"Network error contacting provider: {e}") from e

    if resp.status_code >= 400:
        detail = resp.text[:500]
        try:
            body = resp.json()
            detail = json.dumps(body.get("error", body))[:500]
        except ValueError:
            pass
        raise ProviderError(f"HTTP {resp.status_code} from provider: {detail}")

    try:
        return resp.json()
    except ValueError as e:
        raise ProviderError(f"Provider returned non-JSON response: {e}") from e


def _openai_compatible(base_url, api_key, model, system, user, max_tokens, extra_headers=None):
    """OpenAI, OpenRouter, Groq, xAI and any custom OpenAI-compatible endpoint."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = _post(f"{base_url}/chat/completions", headers, payload)

    choices = data.get("choices") or []
    if not choices:
        raise ProviderError(f"No choices in response: {json.dumps(data)[:300]}")
    return (choices[0].get("message") or {}).get("content") or ""


def _anthropic(base_url, api_key, model, system, user, max_tokens):
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    data = _post(f"{base_url}/messages", headers, payload)

    if data.get("stop_reason") == "refusal":
        raise ProviderError("Claude declined this request.")

    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    if not parts:
        raise ProviderError(f"No text content in response: {json.dumps(data)[:300]}")
    return "".join(parts)


def _gemini(base_url, api_key, model, system, user, max_tokens):
    model_path = model if model.startswith("models/") else f"models/{model}"
    url = f"{base_url}/{model_path}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens},
    }
    data = _post(url, {"x-goog-api-key": api_key, "Content-Type": "application/json"}, payload)

    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback") or {}
        raise ProviderError(f"Gemini returned no candidates. {json.dumps(feedback)[:300]}")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    if not text:
        raise ProviderError(
            f"Gemini returned empty text (finishReason="
            f"{candidates[0].get('finishReason')})."
        )
    return text


def complete(system, user, max_tokens=16000, provider=None, cfg=None):
    """
    Send one prompt to the configured provider and return the raw text.

    Raises ProviderError with a message meant for the extension's status line.
    """
    cfg = cfg if cfg is not None else config.load()
    provider = provider or cfg.get("provider") or "gemini"

    if provider not in config.PROVIDERS:
        raise ProviderError(f"Unknown provider '{provider}'.")

    label = config.PROVIDERS[provider][0]
    api_key = config.get_api_key(provider, cfg)
    if not api_key:
        env_var = config.PROVIDERS[provider][1]
        raise ProviderError(
            f"No API key for {label}. Add one in the extension settings "
            f"(or set {env_var})."
        )

    model = config.get_model(provider, cfg)
    if not model:
        raise ProviderError(f"No model set for {label}. Set one in the extension settings.")

    base_url = config.get_base_url(provider, cfg)
    if not base_url:
        raise ProviderError(
            f"No base URL for {label}. Set the custom base URL in the extension settings."
        )

    if provider == "gemini":
        return _gemini(base_url, api_key, model, system, user, max_tokens)
    if provider == "anthropic":
        return _anthropic(base_url, api_key, model, system, user, max_tokens)

    extra = None
    if provider == "openrouter":
        extra = {"HTTP-Referer": "https://github.com/VrajVaghela/autoclassroom",
                 "X-Title": "AutoClassroom"}
    return _openai_compatible(base_url, api_key, model, system, user, max_tokens, extra)


def test(provider, cfg=None):
    """Cheap round-trip so the settings panel can verify a key works."""
    text = complete(
        "You are a connectivity check. Reply with the single word OK.",
        "Reply with the single word OK.",
        max_tokens=16,
        provider=provider,
        cfg=cfg,
    )
    return text.strip()[:40]
