"""
config.py - Central configuration for Job Application Tracker.

Priority:
  1. Environment variables (highest)
  2. config.json (for non-sensitive settings only)
  3. Hardcoded defaults (lowest)

IMPORTANT: API_KEY is ONLY read from environment variables, never from disk.
Never write API keys to config.json.
"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_JSON = BASE_DIR / "config.json"

# ── Load config.json if present ───────────────────────────────────────────────
def _load_json_config() -> dict:
    if CONFIG_JSON.exists():
        try:
            data = json.loads(CONFIG_JSON.read_text())
            # SECURITY: Never allow api_key in config.json
            data.pop("api_key", None)
            return data
        except Exception:
            pass
    return {}

_jc = _load_json_config()

# ── Paths ─────────────────────────────────────────────────────────────────────
import sys
import platform

# Determine app data directory based on platform
if getattr(sys, 'frozen', False):  # Running as bundled app
    if platform.system() == 'Darwin':  # macOS
        APP_DATA_DIR = Path.home() / "Library" / "Application Support" / "JobTracker"
    elif platform.system() == 'Windows':
        APP_DATA_DIR = Path(os.getenv('APPDATA', Path.home())) / "JobTracker"
    else:  # Linux
        APP_DATA_DIR = Path.home() / ".local" / "share" / "JobTracker"
else:  # Running from source
    APP_DATA_DIR = BASE_DIR

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH          = os.getenv("JOB_TRACKER_DB",    str(APP_DATA_DIR / "jobs.db"))
CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS", str(APP_DATA_DIR / "credentials.json"))
TOKEN_PATH       = os.getenv("GMAIL_TOKEN",        str(APP_DATA_DIR / "token.json"))

# ── Gmail ─────────────────────────────────────────────────────────────────────
GMAIL_SCOPES      = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_MAX_RESULTS = int(os.getenv("GMAIL_MAX_RESULTS", "200"))

# ── Model (env var wins, then config.json, then default) ──────────────────────
MODEL_PROVIDER  = os.getenv("MODEL_PROVIDER", _jc.get("provider", "ollama"))
MODEL_NAME      = os.getenv("MODEL_NAME", _jc.get("model", "mistral:latest"))
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", _jc.get("ollama_endpoint", "http://localhost:11434/api/generate"))
OLLAMA_TIMEOUT  = int(os.getenv("OLLAMA_TIMEOUT", "120"))

# ── API Key (ENVIRONMENT VARIABLE ONLY) ───────────────────────────────────────
# NEVER stored on disk. Must be set via environment before running.
API_KEY = os.getenv("MODEL_API_KEY", "")
API_KEY_PATH = os.getenv("JOB_TRACKER_API_KEY_FILE", str(APP_DATA_DIR / "api_key.enc"))
# ── Status ────────────────────────────────────────────────────────────────────
VALID_STATUSES = {"Applied", "Under Review", "Interview", "Rejected", "Offered", "Other"}
STATUS_PRIORITY: dict[str, int] = {
    "Other":        0,
    "Applied":      1,
    "Under Review": 2,
    "Interview":    3,
    "Offered":      4,
    "Rejected":     4,
}

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL  = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# ── Ollama resource limits (M2 8 GB safe defaults) ────────────────────────────
import multiprocessing as _mp
_logical_cpus = _mp.cpu_count() or 4
OLLAMA_NUM_THREADS = int(os.getenv("OLLAMA_NUM_THREADS", max(1, _logical_cpus - 2)))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "1024"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "128"))
OLLAMA_INTER_EMAIL_DELAY = float(os.getenv("OLLAMA_INTER_EMAIL_DELAY", "1.5"))

# ── Validation ────────────────────────────────────────────────────────────────
def validate_config() -> list[str]:
    """Validate configuration and return list of warnings/errors."""
    issues = []
    
    if MODEL_PROVIDER not in ["ollama", "openai", "gemini", "anthropic"]:
        issues.append(f"Unknown MODEL_PROVIDER: {MODEL_PROVIDER}")
    
    # API key can come from environment OR secure storage, so don't warn if missing yet
    # (it may be set in the UI and stored in keyring/encrypted file)
    
    # Validate numeric ranges
    try:
        timeout = int(os.getenv("OLLAMA_TIMEOUT", "120"))
        if timeout < 10 or timeout > 600:
            issues.append("OLLAMA_TIMEOUT should be 10-600 seconds")
    except ValueError:
        issues.append("OLLAMA_TIMEOUT must be an integer")
    
    return issues
