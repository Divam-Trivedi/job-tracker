"""
server.py - Flask backend for the Job Tracker web UI.

Security features:
  - CSRF token validation on POST/PUT/DELETE
  - JSON schema validation using Pydantic
  - Session management with secure cookies
  - API key stored securely (keyring or encrypted file, never on disk as plaintext)

Endpoints:
  GET  /                        → index.html
  GET  /api/csrf-token          → CSRF token for session
  GET  /api/jobs                → all jobs as JSON
  POST /api/jobs                → create job manually
  PUT  /api/jobs/<id>           → update job
  DELETE /api/jobs/<id>         → delete job
  DELETE /api/jobs              → delete ALL jobs
  GET  /api/config              → current config (api_key masked)
  POST /api/config              → save config (API key to secure storage)
  POST /api/test-connection     → test model connection
  POST /api/run                 → start pipeline run
  POST /api/stop                → cancel running pipeline
  GET  /api/progress            → pipeline progress
  GET  /api/checkpoint          → last checkpoint info
  GET  /api/gmail-user          → authenticated Gmail user email
  POST /api/gmail-reconnect     → clear Gmail token
"""

import json
import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory
from pydantic import BaseModel, ValidationError

import config as cfg
from database import Database
from main import export_csv, export_json, resolve_since, run_pipeline
from ollama_classifier import Classifier
from api_key_storage import get_api_key, save_api_key

logging.basicConfig(level=logging.INFO, format=cfg.LOG_FORMAT)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
CONFIG_JSON = BASE_DIR / "config.json"

app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")

# ══════════════════════════════════════════════════════════════════════════════
# Security: Session & CSRF
# ══════════════════════════════════════════════════════════════════════════════

class SessionStore:
    """Simple in-memory session store with CSRF tokens."""
    def __init__(self):
        self.sessions = {}  # session_id -> {csrf_token, created_at}
    
    def create_session(self) -> str:
        """Create new session with CSRF token. Returns session ID."""
        session_id = secrets.token_hex(16)
        csrf_token = secrets.token_hex(16)
        self.sessions[session_id] = {
            "csrf_token": csrf_token,
            "created_at": datetime.now(timezone.utc),
        }
        return session_id
    
    def get_csrf_token(self, session_id: str) -> Optional[str]:
        """Get CSRF token for session. Returns None if expired/not found."""
        if session_id not in self.sessions:
            return None
        session = self.sessions[session_id]
        # Expire sessions after 24 hours
        age = datetime.now(timezone.utc) - session["created_at"]
        if age > timedelta(hours=24):
            del self.sessions[session_id]
            return None
        return session["csrf_token"]
    
    def validate_csrf(self, session_id: str, token: str) -> bool:
        """Validate CSRF token. Returns True if valid."""
        expected = self.get_csrf_token(session_id)
        if not expected:
            return False
        return secrets.compare_digest(token, expected)

_sessions = SessionStore()

def _get_or_create_session():
    """Get session ID from cookie, or create new one."""
    session_id = request.cookies.get("__job_tracker_session")
    if not session_id or session_id not in _sessions.sessions:
        session_id = _sessions.create_session()
    return session_id

def _require_csrf_token(f):
    """Decorator: require CSRF token on POST/PUT/DELETE."""
    def wrapper(*args, **kwargs):
        session_id = _get_or_create_session()
        
        # GET requests don't need CSRF (they're read-only)
        if request.method == "GET":
            return f(*args, **kwargs)
        
        # POST/PUT/DELETE require CSRF token
        token = request.headers.get("X-CSRF-Token", "")
        if not token or not _sessions.validate_csrf(session_id, token):
            logger.warning("CSRF validation failed for %s %s", request.method, request.path)
            return jsonify({"error": "CSRF token invalid or missing"}), 403
        
        return f(*args, **kwargs)
    
    wrapper.__name__ = f.__name__
    return wrapper

# ══════════════════════════════════════════════════════════════════════════════
# Pydantic Models
# ══════════════════════════════════════════════════════════════════════════════

class JobCreate(BaseModel):
    company: str
    role: str
    status: str
    applied_date: str
    
    class Config:
        str_strip_whitespace = True

class JobUpdate(BaseModel):
    company: str
    role: str
    status: str
    applied_date: str
    
    class Config:
        str_strip_whitespace = True

class ConfigSave(BaseModel):
    provider: str
    model: str
    ollama_endpoint: str
    api_key: Optional[str] = ""
    
    class Config:
        str_strip_whitespace = True

class TestConnection(BaseModel):
    provider: str
    model: str
    ollama_endpoint: str
    api_key: Optional[str] = ""
    
    class Config:
        str_strip_whitespace = True

class RunRequest(BaseModel):
    mode: str = "hours"  # checkpoint | hours | date
    hours: Optional[int] = 36
    since_date: Optional[str] = None

def _parse_json(schema_class):
    """Parse and validate request JSON against Pydantic schema."""
    try:
        data = request.get_json(force=True)
        return schema_class(**data), None
    except ValidationError as e:
        return None, (jsonify({"error": f"Invalid request: {e.errors()}"}), 400)
    except Exception as e:
        return None, (jsonify({"error": f"Malformed JSON: {str(e)}"}), 400)

# ══════════════════════════════════════════════════════════════════════════════
# Run state
# ══════════════════════════════════════════════════════════════════════════════

_run_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_progress: dict = {}  # shared dict written by pipeline thread, read by /api/progress

def _is_running() -> bool:
    return _run_thread is not None and _run_thread.is_alive()

# ══════════════════════════════════════════════════════════════════════════════
# Static
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    """Serve index.html with CSRF token in session."""
    session_id = _get_or_create_session()
    csrf_token = _sessions.get_csrf_token(session_id)
    
    response = send_from_directory(str(BASE_DIR), "index.html")
    response.set_cookie(
        "__job_tracker_session",
        session_id,
        max_age=86400,  # 24 hours
        httponly=True,
        samesite="Strict",
        secure=False,  # localhost; set True for HTTPS
    )
    
    return response

# ══════════════════════════════════════════════════════════════════════════════
# CSRF Token endpoint
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/csrf-token", methods=["GET"])
def get_csrf_token():
    """Return current CSRF token for frontend."""
    session_id = _get_or_create_session()
    csrf_token = _sessions.get_csrf_token(session_id)
    return jsonify({"csrf_token": csrf_token})

# ══════════════════════════════════════════════════════════════════════════════
# Jobs API
# ══════════════════════════════════════════════════════════════════════════════

def _job_dict(job) -> dict:
    return {
        "id": job.id,
        "company": job.company,
        "role": job.role,
        "status": job.status,
        "applied_date": job.applied_date,
        "last_updated": job.last_updated,
    }

@app.route("/api/jobs", methods=["GET"])
def get_jobs():
    db = Database()
    return jsonify([_job_dict(j) for j in db.all_jobs()])

@app.route("/api/jobs", methods=["POST"])
@_require_csrf_token
def create_job():
    job_data, error_response = _parse_json(JobCreate)
    if error_response:
        return error_response
    
    if job_data.status not in cfg.VALID_STATUSES:
        return jsonify({"error": f"Invalid status: {job_data.status}"}), 400
    
    db = Database()
    job = db.insert_job(
        company=job_data.company,
        role=job_data.role,
        status=job_data.status,
        applied_date=job_data.applied_date,
    )
    export_json(db)
    export_csv(db)
    return jsonify(_job_dict(job)), 201

@app.route("/api/jobs/<int:job_id>", methods=["PUT"])
@_require_csrf_token
def update_job(job_id: int):
    job_data, error_response = _parse_json(JobUpdate)
    if error_response:
        return error_response
    
    if job_data.status not in cfg.VALID_STATUSES:
        return jsonify({"error": f"Invalid status: {job_data.status}"}), 400
    
    db = Database()
    job = db.update_job(
        job_id=job_id,
        company=job_data.company,
        role=job_data.role,
        status=job_data.status,
        applied_date=job_data.applied_date,
    )
    if job is None:
        return jsonify({"error": "Not found"}), 404
    export_json(db)
    export_csv(db)
    return jsonify(_job_dict(job))

@app.route("/api/jobs/<int:job_id>", methods=["DELETE"])
@_require_csrf_token
def delete_job(job_id: int):
    db = Database()
    if not db.delete_job(job_id):
        return jsonify({"error": "Not found"}), 404
    export_json(db)
    export_csv(db)
    return jsonify({"ok": True})

@app.route("/api/jobs", methods=["DELETE"])
@_require_csrf_token
def delete_all_jobs():
    """Delete ALL jobs after confirmation."""
    db = Database()
    try:
        all_jobs = db.all_jobs()
        for job in all_jobs:
            db.delete_job(job.id)
        export_json(db)
        export_csv(db)
        return jsonify({"ok": True, "deleted": len(all_jobs)})
    except Exception as e:
        logger.error("Error deleting all jobs: %s", e)
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
# Gmail User Info
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/gmail-user", methods=["GET"])
def get_gmail_user():
    """Get the authenticated Gmail user's email address."""
    try:
        from gmail_client import GmailClient
        gmail = GmailClient()
        creds = gmail._get_credentials()
        service = gmail._service_client()
        profile = service.users().getProfile(userId='me').execute()
        email_address = profile.get('emailAddress', 'unknown')
        return jsonify({"email": email_address, "ok": True})
    except FileNotFoundError as e:
        return jsonify({"email": None, "ok": False, "error": str(e)}), 401
    except Exception as e:
        logger.error("Error getting Gmail user: %s", e)
        return jsonify({"email": None, "ok": False, "error": str(e)}), 500

@app.route("/api/gmail-reconnect", methods=["POST"])
@_require_csrf_token
def gmail_reconnect():
    """Force Gmail re-authentication (delete token)."""
    try:
        from gmail_client import GmailClient
        gmail = GmailClient()
        gmail.clear_token()
        logger.info("Gmail token cleared, forcing re-auth on next run")
        return jsonify({"ok": True, "message": "Token cleared. Please refresh the page."})
    except Exception as e:
        logger.error("Error clearing Gmail token: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
# Config API
# ══════════════════════════════════════════════════════════════════════════════

def _safe_config(raw: dict) -> dict:
    """Return config with api_key masked for the UI."""
    out = dict(raw)
    # Show indicator if key exists in secure storage
    if get_api_key():
        out["api_key"] = "••••••••"  # Masked but indicates it's set
    else:
        out["api_key"] = ""
    return out

@app.route("/api/config", methods=["GET"])
def get_config():
    if CONFIG_JSON.exists():
        try:
            raw = json.loads(CONFIG_JSON.read_text())
            return jsonify(_safe_config(raw))
        except Exception:
            pass
    return jsonify({
        "provider":        cfg.MODEL_PROVIDER,
        "model":           cfg.MODEL_NAME,
        "ollama_endpoint": cfg.OLLAMA_ENDPOINT,
        "api_key":         "",
    })

@app.route("/api/config", methods=["POST"])
@_require_csrf_token
def save_config():
    """
    Save configuration.
    
    SECURITY: API key is stored securely using OS keyring (with encrypted file fallback).
    It is NEVER written to disk as plaintext. The api_key field in config.json is unused.
    """
    config_data, error_response = _parse_json(ConfigSave)
    if error_response:
        return error_response
    
    # Load existing config (don't wipe unrelated settings)
    existing: dict = {}
    if CONFIG_JSON.exists():
        try:
            existing = json.loads(CONFIG_JSON.read_text())
        except Exception:
            pass
    
    # SECURITY: Never save api_key to disk
    existing.pop("api_key", None)
    
    # Update only non-sensitive fields
    existing["provider"]        = config_data.provider
    existing["model"]           = config_data.model
    existing["ollama_endpoint"] = config_data.ollama_endpoint
    
    try:
        # Atomic write: write to temp file, then rename
        temp_path = CONFIG_JSON.with_suffix(".tmp")
        temp_path.write_text(json.dumps(existing, indent=2))
        temp_path.replace(CONFIG_JSON)
        
        # Store API key securely (keyring or encrypted file, never plaintext on disk)
        if config_data.api_key:
            save_api_key(config_data.api_key)
            logger.info("API key saved to secure storage (keyring or encrypted file)")
        
        return jsonify({"ok": True})
    except Exception as e:
        logger.error("Failed to save config: %s", e)
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
# Test connection
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/test-connection", methods=["POST"])
@_require_csrf_token
def test_connection():
    test_data, error_response = _parse_json(TestConnection)
    if error_response:
        return error_response
    
    # Priority: environment variable > secure storage > request body
    api_key = cfg.API_KEY or get_api_key() or test_data.api_key or ""
    
    overrides = {
        "provider":        test_data.provider,
        "model":           test_data.model,
        "ollama_endpoint": test_data.ollama_endpoint,
        "api_key":         api_key,
    }
    classifier = Classifier(overrides=overrides)
    ok, msg = classifier.test_connection()
    return jsonify({"ok": ok, "message": msg})

# ══════════════════════════════════════════════════════════════════════════════
# Checkpoint
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/checkpoint", methods=["GET"])
def get_checkpoint():
    db = Database()
    cp = db.get_checkpoint()
    return jsonify({"checkpoint": cp})

# ══════════════════════════════════════════════════════════════════════════════
# Run / Stop / Progress
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/run", methods=["POST"])
@_require_csrf_token
def start_run():
    global _run_thread, _stop_event, _progress

    if _is_running():
        return jsonify({"error": "A run is already in progress"}), 409

    run_data, error_response = _parse_json(RunRequest)
    if error_response:
        return error_response

    db = Database()
    db.clear_processed_emails()

    if run_data.mode == "checkpoint":
        since = resolve_since(None, use_checkpoint=True, db=db)
    elif run_data.mode == "date" and run_data.since_date:
        since = resolve_since(run_data.since_date, use_checkpoint=False, db=db)
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=run_data.hours or 36)

    _stop_event = threading.Event()
    _progress = {"done": 0, "total": 0, "status": "starting", "counts": {}}
    
    # Get API key from secure storage (keyring or encrypted file)
    api_key = cfg.API_KEY or get_api_key() or ""

    def _worker():
        try:
            _progress["status"] = "running"
            
            def progress_cb(done, total, outcome):
                _progress["done"] = done
                _progress["total"] = total
                _progress["counts"][outcome] = _progress["counts"].get(outcome, 0) + 1

            counts = run_pipeline(
                since=since,
                db=db,
                stop_flag=_stop_event.is_set,
                progress_cb=progress_cb,
                api_key=api_key,
            )
            _progress["counts"] = counts
            _progress["status"] = "cancelled" if counts.get("cancelled") else "done"
        except Exception as exc:
            logger.exception("Pipeline error")
            _progress["status"] = "error"
            _progress["error"] = str(exc)

    _run_thread = threading.Thread(target=_worker, daemon=True)
    _run_thread.start()
    return jsonify({"ok": True})

@app.route("/api/stop", methods=["POST"])
@_require_csrf_token
def stop_run():
    if not _is_running():
        return jsonify({"ok": True, "message": "Nothing running"})
    _stop_event.set()
    return jsonify({"ok": True})

@app.route("/api/progress", methods=["GET"])
def get_progress():
    """Poll endpoint for run progress."""
    return jsonify({**_progress, "running": _is_running()})

# ══════════════════════════════════════════════════════════════════════════════
# Error handling
# ══════════════════════════════════════════════════════════════════════════════

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    logger.exception("Internal server error")
    return jsonify({"error": "Internal server error"}), 500

# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"\n  Job Tracker → http://{args.host}:{args.port}\n")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)