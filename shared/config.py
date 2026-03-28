"""
shared/config.py

Central loading of all API keys from .env and runtime config from config.toml.
All tool modules import from here — never call os.getenv() directly in tools.
"""

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env")

# ── Config file ───────────────────────────────────────────────────────────────


def _load_config() -> dict:
    path = _ROOT / "config.toml"
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


_CFG = _load_config()


def _cfg(section: str, key: str, default):
    """Read a value from the config file with a fallback default."""
    return _CFG.get(section, {}).get(key, default)


# ── Env helpers (API keys only) ───────────────────────────────────────────────


def _get_env(key: str) -> str:
    return os.getenv(key, "")


def missing_key_error_env(key: str) -> str:
    return f"Configuration error: {key} is not set. Please add it to your .env file."


# ── API Keys (stay in .env) ───────────────────────────────────────────────────

HUNTER_API_KEY = _get_env("HUNTER_API_KEY")
SECURITYTRAILS_KEY = _get_env("SECURITYTRAILS_KEY")
SHODAN_KEY = _get_env("SHODAN_KEY")
ABUSEIPDB_KEY = _get_env("ABUSEIPDB_KEY")
VIRUSTOTAL_KEY = _get_env("VIRUSTOTAL_KEY")
HIBP_KEY = _get_env("HIBP_KEY")
LEAKCHECK_KEY = _get_env("LEAKCHECK_KEY")
INTELX_KEY = _get_env("INTELX_KEY")
IPINFO_TOKEN = _get_env("IPINFO_TOKEN")
WHOISXML_KEY = _get_env("WHOISXML_KEY")
OPENCORPORATES_KEY = _get_env("OPENCORPORATES_KEY")
ETHERSCAN_KEY = _get_env("ETHERSCAN_KEY")
BLOCKCHAIR_KEY = _get_env("BLOCKCHAIR_KEY")
OPENCAGE_KEY = _get_env("OPENCAGE_KEY")
EMAILREP_KEY = _get_env("EMAILREP_KEY")
ADZUNA_APP_ID = _get_env("ADZUNA_APP_ID")
ADZUNA_API_KEY = _get_env("ADZUNA_API_KEY")
GOOGLE_VISION_KEY = _get_env("GOOGLE_VISION_KEY")
TINEYE_KEY = _get_env("TINEYE_KEY")
NEWSAPI_KEY = _get_env("NEWSAPI_KEY")
FULLCONTACT_KEY = _get_env("FULLCONTACT_KEY")
ALCHEMY_KEY = _get_env("ALCHEMY_KEY")
OPENSEA_KEY = _get_env("OPENSEA_KEY")
NUMVERIFY_KEY = _get_env("NUMVERIFY_KEY")
NORTHDATA_KEY = _get_env("NORTHDATA_KEY")
BUNDESTAG_DIP_KEY = _get_env("BUNDESTAG_DIP_KEY")
GITHUB_TOKEN = _get_env("GITHUB_TOKEN")
REDDIT_CLIENT_ID = _get_env("REDDIT_CLIENT_ID")
REDDIT_SECRET = _get_env("REDDIT_SECRET")
IPHUB_KEY = _get_env("IPHUB_KEY")
WAPPALYZER_KEY = _get_env("WAPPALYZER_KEY")
SAUCENAO_KEY = _get_env("SAUCENAO_KEY")
COURT_LISTENER_KEY = _get_env("COURT_LISTENER_KEY")
TWITTER_BEARER_TOKEN = _get_env("TWITTER_BEARER_TOKEN")
TWITCH_CLIENT_ID = _get_env("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = _get_env("TWITCH_CLIENT_SECRET")
YOUTUBE_API_KEY = _get_env("YOUTUBE_API_KEY")
VK_ACCESS_TOKEN = _get_env("VK_ACCESS_TOKEN")
SPOTIFY_CLIENT_ID = _get_env("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = _get_env("SPOTIFY_CLIENT_SECRET")
LASTFM_API_KEY = _get_env("LASTFM_API_KEY")
SOUNDCLOUD_CLIENT_ID = _get_env("SOUNDCLOUD_CLIENT_ID")
TUMBLR_API_KEY = _get_env("TUMBLR_API_KEY")
FLICKR_API_KEY = _get_env("FLICKR_API_KEY")
GRAVATAR_API_KEY = _get_env("GRAVATAR_API_KEY")
STACKEXCHANGE_API_KEY = _get_env("STACKEXCHANGE_API_KEY")
OCR_SPACE_KEY = _get_env("OCR_SPACE_KEY")

# Account(s)
INSTAGRAM_USERNAME = _get_env("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = _get_env("INSTAGRAM_PASSWORD")


# ── Runtime defaults (from config.toml, env override still honoured for model) ──

_runtime = _CFG.get("runtime", {})

DEFAULT_MODEL_FALLBACK = _runtime.get("default_model_fallback")
DEFAULT_MODEL = _get_env("OSINT_MODEL") or DEFAULT_MODEL_FALLBACK
DEFAULT_SCOPE_MODE = _runtime.get("scope_mode", "guided")
DEFAULT_MAX_TOOL_CALLS = _runtime.get("max_tool_calls", 64)
DEFAULT_MAX_CONTEXT_TOKENS = _runtime.get("max_context_tokens", 0)
DEFAULT_COMPRESSION_THRESHOLD = _runtime.get("compression_threshold", 0.82)
DEFAULT_EVENT_LOG_SIZE = _runtime.get("event_log_size", 80)
DEFAULT_MAX_REPORT_GRACE_ROUNDS = _runtime.get("max_report_grace_rounds", 2)

REDDIT_USER_AGENT = _runtime.get("reddit_user_agent") or "cosint/1.0.0.beta.1"


# http_client
_httpc = _CFG.get("http_client", {})

HTTP_CLIENT_DEFAULT_TIMEOUT = _httpc.get("default_timeout", 20)
HTTP_CLIENT_MAX_RETRIES = _httpc.get("max_retries", 3)
HTTP_CLIENT_RETRY_BACKOFF = _httpc.get("retry_backoff", 1.5)
HTTP_CLIENT_GET_CACHE_TTL_SECONDS = _httpc.get("get_cache_ttl_seconds", 120)
HTTP_CLIENT_GET_CACHE_MAX_ENTRIES = _httpc.get("get_cache_max_entries", 256)

# browser playwright
_browser = _CFG.get("browser", {})

BROWSER_LOCALE = _browser.get("locale", "en-US")
BROWSER_TIMEZONE = _browser.get("timezone", "US/Pacific")
BROWSER_USER_AGENT = _browser.get("user_agent")

BROWSER_CAPTCHA_POLL = _browser.get("captcha_poll", 3_000)
BROWSER_RESULT_WAIT = _browser.get("result_wait", 8_000)
BROWSER_CAPTCHA_SOLVE = _browser.get("captcha_solve", 90_000)

# LLM
_llm = _CFG.get("llm", {})
LLM_MAX_RETRIES = _llm.get("max_retries", 3)
LLM_RETRY_BACKOFF = _llm.get("retry_backoff", 1.5)

# Subprocess
_subprocess = _CFG.get("subprocess", {})
SUBPROCESS_DEFAULT_TIMEOUT = _subprocess.get("default_timeout", 180)

# Tools
_tools = _CFG.get("tools", {})
CUSTOM_MAIGRET_DB = _tools.get("custom_maigret_db", "")

# Compressor
compressor = _CFG.get("compression", {})
# Defaults tuned to be more aggressive than the previous settings.
# Previous defaults were: max_compression_passes=4, keep_last_max=24, keep_last_min=6
# If you want *less* aggressive compression, increase keep_last_max and/or increase
# the compression threshold in the runtime section.
COMPRESSOR_MAX_COMPRESSION_PASSES = compressor.get("max_compression_passes", 6)
# keep_last_max: smaller -> compressor preserves fewer recent messages (more aggressive)
COMPRESSOR_KEEP_LAST_MAX = compressor.get("keep_last_max", 12)
# keep_last_min: smaller -> minimum tail is smaller (more aggressive)
COMPRESSOR_KEEP_LAST_MIN = compressor.get("keep_last_min", 3)

# New configurable parameters controlling the summary content size and fallback
# estimator. These were previously hard-coded in the compressor module.
COMPRESSOR_SNIPPET_MAX_LENGTH = compressor.get("snippet_max_length", 350)
COMPRESSOR_SNIPPET_MAX_COUNT = compressor.get("snippet_max_count", 12)
COMPRESSOR_ASSISTANT_INSIGHT_LENGTH = compressor.get("assistant_insight_length", 220)
COMPRESSOR_ASSISTANT_INSIGHT_COUNT = compressor.get("assistant_insight_count", 6)
# Fallback estimator parameters (used when LiteLLM tokenizer is unavailable).
# Lowering chars_per_token -> smaller token estimates -> compression triggers earlier
COMPRESSOR_FALLBACK_CHARS_PER_TOKEN = compressor.get("estimate_chars_per_token", 4)
COMPRESSOR_FALLBACK_MSG_OVERHEAD = compressor.get("estimate_per_message_overhead", 24)

# Safety cap: maximum characters allowed in the generated compressed summary message.
# If the summary would exceed this, it will be truncated. Default is large but bounded.
COMPRESSOR_MAX_SUMMARY_CHARS = compressor.get("max_summary_chars", 4000)

# Pressure factor applied to the adaptive keep_last calculation. Values >1 make the
# compressor tighten the tail more aggressively. Default 1.25 is a mild pressure.
COMPRESSOR_PRESSURE = compressor.get("pressure", 1.25)
