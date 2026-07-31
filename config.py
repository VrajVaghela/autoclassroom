"""
User-editable configuration for AutoClassroom.

Everything the user can change from the extension's settings panel lives in
config.json next to this file. That file is gitignored because it holds API
keys. Reads fall back to environment variables so an existing .env keeps
working without any migration.
"""

import json
import os
import stat
import tempfile

from dotenv import load_dotenv

# Keeps a pre-existing .env working as the fallback for API keys.
load_dotenv()

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# Default output folder. Used until the user picks one in settings.
DEFAULT_OUTPUT_DIR = os.path.join(CONFIG_DIR, "lab")

# provider key -> (label, env var, default model, api base)
PROVIDERS = {
    "gemini": ("Google Gemini", "GEMINI_API_KEY", "gemini-2.0-flash",
               "https://generativelanguage.googleapis.com/v1beta"),
    "openai": ("OpenAI", "OPENAI_API_KEY", "gpt-4o",
               "https://api.openai.com/v1"),
    "anthropic": ("Anthropic Claude", "ANTHROPIC_API_KEY", "claude-opus-5",
                  "https://api.anthropic.com/v1"),
    "openrouter": ("OpenRouter", "OPENROUTER_API_KEY", "openai/gpt-4o",
                   "https://openrouter.ai/api/v1"),
    "groq": ("Groq", "GROQ_API_KEY", "llama-3.3-70b-versatile",
             "https://api.groq.com/openai/v1"),
    "xai": ("xAI Grok", "XAI_API_KEY", "grok-2-latest",
            "https://api.x.ai/v1"),
    "custom": ("Custom (OpenAI-compatible)", "CUSTOM_API_KEY", "",
               ""),
}

DEFAULTS = {
    "output_dir": DEFAULT_OUTPUT_DIR,
    "provider": "gemini",
    # provider key -> model override. Empty string means "use the default".
    "models": {},
    # provider key -> api key. Never sent back to the extension in full.
    "api_keys": {},
    # Base URL for the "custom" provider only.
    "custom_base_url": "",
    # Run generated code locally to capture real output for screenshots.
    # Off by default: the code comes from an LLM reading an untrusted assignment.
    "run_code": False,
    "run_timeout": 20,
}


def _harden(path):
    """Best-effort owner-only permissions. No-op where chmod isn't meaningful."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load():
    """Return the full config, with defaults filled in for missing keys."""
    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError) as e:
            print(f"config.json unreadable ({e}); using defaults.")

    cfg = dict(DEFAULTS)
    cfg["models"] = dict(DEFAULTS["models"])
    cfg["api_keys"] = dict(DEFAULTS["api_keys"])
    for key, value in data.items():
        if key in ("models", "api_keys"):
            if isinstance(value, dict):
                cfg[key].update({k: v for k, v in value.items() if isinstance(v, str)})
        elif key in cfg:
            cfg[key] = value

    if not cfg.get("output_dir"):
        cfg["output_dir"] = DEFAULT_OUTPUT_DIR
    if cfg.get("provider") not in PROVIDERS:
        cfg["provider"] = DEFAULTS["provider"]
    return cfg


def save(cfg):
    """Write config atomically so a crash can't leave a truncated file."""
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        _harden(tmp)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    _harden(CONFIG_PATH)


def update(patch):
    """
    Merge a partial settings update from the extension.

    api_keys and models merge per-provider so the UI can send only what the
    user actually edited. An explicit empty string clears that provider's
    entry (falling back to the environment variable, if any).
    """
    cfg = load()

    if "output_dir" in patch:
        raw = (patch["output_dir"] or "").strip()
        cfg["output_dir"] = os.path.abspath(os.path.expanduser(raw)) if raw else DEFAULT_OUTPUT_DIR

    if "provider" in patch and patch["provider"] in PROVIDERS:
        cfg["provider"] = patch["provider"]

    if "custom_base_url" in patch:
        cfg["custom_base_url"] = (patch["custom_base_url"] or "").strip()

    if "run_code" in patch:
        cfg["run_code"] = bool(patch["run_code"])

    if "run_timeout" in patch:
        try:
            cfg["run_timeout"] = max(1, min(300, int(patch["run_timeout"])))
        except (TypeError, ValueError):
            pass

    for field in ("models", "api_keys"):
        incoming = patch.get(field)
        if not isinstance(incoming, dict):
            continue
        for pkey, value in incoming.items():
            if pkey not in PROVIDERS or not isinstance(value, str):
                continue
            value = value.strip()
            if value:
                cfg[field][pkey] = value
            else:
                cfg[field].pop(pkey, None)

    save(cfg)
    return cfg


def get_api_key(provider, cfg=None):
    """Config key first, then the provider's environment variable."""
    cfg = cfg if cfg is not None else load()
    key = (cfg.get("api_keys") or {}).get(provider, "")
    if key.strip():
        return key.strip()
    env_var = PROVIDERS.get(provider, (None, None, None, None))[1]
    return (os.getenv(env_var, "") if env_var else "").strip()


def get_model(provider, cfg=None):
    cfg = cfg if cfg is not None else load()
    model = (cfg.get("models") or {}).get(provider, "")
    return model.strip() or PROVIDERS.get(provider, ("", "", "", ""))[2]


def get_base_url(provider, cfg=None):
    cfg = cfg if cfg is not None else load()
    if provider == "custom":
        return (cfg.get("custom_base_url") or "").strip().rstrip("/")
    return PROVIDERS.get(provider, ("", "", "", ""))[3]


def mask(key):
    """Show only enough of a key to confirm which one is stored."""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * 8}{key[-4:]}"


def public_view():
    """The settings shape sent to the extension. Keys are masked, never raw."""
    cfg = load()
    providers = []
    for pkey, (label, env_var, default_model, _base) in PROVIDERS.items():
        stored = (cfg.get("api_keys") or {}).get(pkey, "").strip()
        from_env = bool(os.getenv(env_var, "").strip()) if env_var else False
        providers.append({
            "key": pkey,
            "label": label,
            "default_model": default_model,
            "model": (cfg.get("models") or {}).get(pkey, ""),
            "has_key": bool(stored) or from_env,
            "key_from_env": (not stored) and from_env,
            "masked_key": mask(stored),
            "env_var": env_var,
        })
    return {
        "output_dir": cfg["output_dir"],
        "provider": cfg["provider"],
        "custom_base_url": cfg.get("custom_base_url", ""),
        "run_code": bool(cfg.get("run_code")),
        "run_timeout": cfg.get("run_timeout", 20),
        "providers": providers,
    }
