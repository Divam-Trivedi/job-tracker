"""
Usage:
  python main.py                    # last 36 hours
  python main.py --since 2024-01-01
  python main.py --checkpoint       # resume from last saved checkpoint
"""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Callable

import config as cfg
from database import Database
from gmail_client import GmailClient, GmailMessage
from ollama_classifier import Classifier

logging.basicConfig(level=getattr(logging, cfg.LOG_LEVEL, logging.INFO), format=cfg.LOG_FORMAT)
logger = logging.getLogger(__name__)

CSV_PATH  = str(Path(__file__).parent / "jobs.csv")
JSON_PATH = str(Path(__file__).parent / "jobs_data.json")


# ── Since resolution ──────────────────────────────────────────────────────────

def resolve_since(
    since_arg: Optional[str],
    use_checkpoint: bool,
    db: Database,
) -> datetime:
    if use_checkpoint:
        cp = db.get_checkpoint()
        if cp:
            dt = datetime.fromisoformat(cp).replace(tzinfo=timezone.utc)
            logger.info("Resuming from checkpoint: %s", dt)
            return dt
        logger.info("No checkpoint found, falling back to last 36 hours.")

    if since_arg:
        try:
            dt = datetime.strptime(since_arg, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            logger.info("Processing emails since %s", dt.date())
            return dt
        except ValueError:
            logger.error("Invalid date '%s'. Use YYYY-MM-DD.", since_arg)
            sys.exit(1)

    dt = datetime.now(timezone.utc) - timedelta(hours=36)
    logger.info("Defaulting to last 36 hours (since %s)", dt)
    return dt


# ── Per-email processing ──────────────────────────────────────────────────────

def process_email(
    msg: GmailMessage,
    db: Database,
    classifier: Classifier,
) -> str:
    if db.is_processed(msg.message_id):
        return "skipped"

    logger.info("Classifying: %s", msg.subject[:70])
    result = classifier.classify(
        subject=msg.subject,
        sender=msg.sender,
        body=msg.body or msg.snippet,
        direction=msg.direction,
    )

    # Mark processed and advance checkpoint regardless of classification outcome
    db.mark_processed(msg.message_id, email_date=msg.date)

    if result is None:
        return "error"
    if not result.is_job_email or (not result.company and not result.role):
        return "not_job"

    company = result.company or "Unknown Company"
    role    = result.role    or "Unknown Role"

    action, _ = db.upsert_job(
        company=company,
        role=role,
        status=result.status,
        applied_date=msg.applied_date,  # ✅ USE applied_date, not sliced date
    )
    return action


# ── Main pipeline (callable from server.py) ───────────────────────────────────

def run_pipeline(
    since: datetime,
    db: Database,
    stop_flag: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    api_key: str = "",
) -> dict:
    gmail = GmailClient()
    
    import json
    from pathlib import Path
    config_json = Path(cfg.BASE_DIR) / "config.json"
    overrides = {}
    if config_json.exists():
        try:
            saved_cfg = json.loads(config_json.read_text())
            overrides = {
                "provider": saved_cfg.get("provider", cfg.MODEL_PROVIDER),
                "model": saved_cfg.get("model", cfg.MODEL_NAME),
                "ollama_endpoint": saved_cfg.get("ollama_endpoint", cfg.OLLAMA_ENDPOINT),
                "api_key": api_key or cfg.API_KEY or "",
            }
        except Exception:
            overrides = {"api_key": api_key or cfg.API_KEY or ""}
    
    classifier = Classifier(overrides=overrides)

    emails = gmail.fetch_emails_since(since)
    total  = len(emails)
    logger.info("Found %d emails to process", total)

    counts: dict[str, int] = {
        "total": total,
        "skipped": 0, "not_job": 0,
        "created": 0, "updated": 0,
        "unchanged": 0, "error": 0,
    }

    for i, msg in enumerate(emails, 1):
        if stop_flag and stop_flag():
            logger.info("Run cancelled by user at email %d/%d", i, total)
            counts["cancelled"] = True
            break

        outcome = process_email(msg, db, classifier)
        counts[outcome] = counts.get(outcome, 0) + 1

        if progress_cb:
            progress_cb(i, total, outcome)

    export_json(db)
    export_csv(db)
    return counts


# ── Exports ───────────────────────────────────────────────────────────────────

def export_json(db: Database, path: str = JSON_PATH) -> None:
    """Export jobs to JSON (safe; JSON handles escaping)."""
    jobs = db.all_jobs()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checkpoint":   db.get_checkpoint(),
        "stats":        db.stats(),
        "jobs": [
            {
                "id": j.id, "company": j.company, "role": j.role,
                "status": j.status, "applied_date": j.applied_date,
                "last_updated": j.last_updated,
            }
            for j in jobs
        ],
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logger.info("Exported JSON: %s", path)
    except Exception as e:
        logger.error("Failed to export JSON: %s", e)

def export_csv(db: Database, path: str = CSV_PATH) -> None:
    """
    Export jobs to CSV with proper escaping.
    
    SECURITY: Use QUOTE_ALL to prevent CSV injection attacks.
    Even if company/role contains formula prefixes (=, +, -, @),
    quoted fields won't be executed by spreadsheet applications.
    """
    jobs = db.all_jobs()
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            # QUOTE_ALL ensures all fields are quoted, preventing formula injection
            w = csv.DictWriter(
                f,
                fieldnames=["id", "company", "role", "status", "applied_date", "last_updated"],
                quoting=csv.QUOTE_ALL,  # SECURITY: Prevent CSV injection
            )
            w.writeheader()
            for j in jobs:
                w.writerow({
                    "id": j.id,
                    "company": j.company,
                    "role": j.role,
                    "status": j.status,
                    "applied_date": j.applied_date,
                    "last_updated": j.last_updated,
                })
        logger.info("Exported CSV: %s", path)
    except Exception as e:
        logger.error("Failed to export CSV: %s", e)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Gmail Job Application Tracker")
    parser.add_argument("--since",      metavar="DATE", help="YYYY-MM-DD")
    parser.add_argument("--checkpoint", action="store_true", help="Resume from last checkpoint")
    parser.add_argument("--db",         default=cfg.DB_PATH)
    args = parser.parse_args()

    db    = Database(db_path=args.db)
    since = resolve_since(args.since, args.checkpoint, db)

    counts = run_pipeline(since, db)

    print(f"\n  Processed {counts['total']} emails")
    print(f"  New: {counts.get('created',0)}  Updated: {counts.get('updated',0)}  "
          f"Skipped: {counts.get('skipped',0)}  Non-job: {counts.get('not_job',0)}\n")


if __name__ == "__main__":
    main()
