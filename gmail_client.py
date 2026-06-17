"""
gmail_client.py - Gmail API integration with secure token storage.

Token storage:
  - Linux: libsecret (GNOME Keyring)
  - macOS: Keychain
  - Windows: Windows Credential Manager
  - Fallback: Encrypted file (if keyring unavailable)

Fetches both received (Primary inbox) and sent emails, tagging each
with a direction field so the classifier can apply appropriate rules.
"""

import base64
import email as email_lib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import CREDENTIALS_PATH, GMAIL_MAX_RESULTS, GMAIL_SCOPES, TOKEN_PATH

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Secure Token Storage (Keyring)
# ──────────────────────────────────────────────────────────────────────────────

_KEYRING_SERVICE = "job-tracker"
_KEYRING_USER    = "gmail-token"

def _try_import_keyring():
    """Try to import keyring. Return module or None if unavailable."""
    try:
        import keyring
        return keyring
    except ImportError:
        return None

def _get_token_from_keyring() -> Optional[str]:
    """Retrieve OAuth token from system keyring. Returns None if not found."""
    keyring = _try_import_keyring()
    if not keyring:
        return None
    
    try:
        token = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        if token:
            logger.debug("Token retrieved from keyring")
        return token
    except Exception as e:
        logger.warning("Keyring retrieval failed: %s", e)
        return None

def _save_token_to_keyring(token_json: str) -> bool:
    """Save OAuth token to system keyring. Returns True if successful."""
    keyring = _try_import_keyring()
    if not keyring:
        logger.warning("Keyring not available; token will not be saved securely")
        return False
    
    try:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, token_json)
        logger.info("Token saved to system keyring")
        return True
    except Exception as e:
        logger.warning("Keyring save failed: %s", e)
        return False

def _delete_token_from_keyring() -> bool:
    """Delete OAuth token from keyring. Returns True if successful."""
    keyring = _try_import_keyring()
    if not keyring:
        return False
    
    try:
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USER)
        logger.info("Token deleted from keyring")
        return True
    except Exception as e:
        logger.debug("Keyring delete failed: %s (may not exist)", e)
        return False

def _get_token_from_disk() -> Optional[str]:
    """Fallback: read token from encrypted file on disk. (Simple XOR, not production crypto)."""
    token_path = Path(TOKEN_PATH)
    if not token_path.exists():
        return None
    
    try:
        data = token_path.read_bytes()
        # Simple XOR cipher using a fixed key (not production-grade, but better than plaintext)
        # In a real app, use cryptography.fernet or similar
        key = b"job-tracker-token-key"
        decrypted = bytes(a ^ b for a, b in zip(data, key * (len(data) // len(key) + 1)))
        return decrypted.decode('utf-8', errors='ignore')
    except Exception as e:
        logger.warning("Could not read token from disk: %s", e)
        return None

def _save_token_to_disk(token_json: str) -> bool:
    """Fallback: save token to encrypted file on disk."""
    token_path = Path(TOKEN_PATH)
    
    try:
        data = token_json.encode('utf-8')
        # Simple XOR cipher (not production-grade)
        key = b"job-tracker-token-key"
        encrypted = bytes(a ^ b for a, b in zip(data, key * (len(data) // len(key) + 1)))
        token_path.write_bytes(encrypted)
        token_path.chmod(0o600)  # Read-only by owner
        logger.info("Token saved to encrypted file (with basic XOR, not cryptographically secure)")
        return True
    except Exception as e:
        logger.warning("Could not save token to disk: %s", e)
        return False


@dataclass
class GmailMessage:
    message_id: str
    subject: str
    sender: str
    date: str                      # ISO-8601, email's NATIVE timezone (not converted)
    applied_date: str              # YYYY-MM-DD in email's timezone
    body: str
    snippet: str
    direction: str = "received"    # "received" | "sent"


class GmailClient:

    def __init__(
        self,
        credentials_path: str = CREDENTIALS_PATH,
        token_path: str = TOKEN_PATH,
    ) -> None:
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._service = None

    # ── Authentication ────────────────────────────────────────────────────────

    def _get_credentials(self) -> Credentials:
        """
        Get OAuth2 credentials. Token is retrieved from keyring first,
        then fallback to encrypted disk storage.
        """
        creds: Optional[Credentials] = None

        # Try to load from keyring
        token_json = _get_token_from_keyring()
        if not token_json:
            # Fallback to encrypted disk
            token_json = _get_token_from_disk()

        if token_json:
            try:
                creds = Credentials.from_authorized_user_info(json.loads(token_json), GMAIL_SCOPES)
            except Exception as e:
                logger.warning("Could not deserialize token: %s", e)
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired Gmail token …")
                creds.refresh(Request())
            else:
                if not Path(self.credentials_path).exists():
                    raise FileNotFoundError(
                        f"Gmail credentials not found at {self.credentials_path}.\n"
                        "Download credentials.json from the Google Cloud Console and "
                        "place it in the project root or the app directory."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, GMAIL_SCOPES
                )
                creds = flow.run_local_server(port=0)
                logger.info("OAuth2 flow completed, new token obtained.")

            # Save token securely (keyring first, fallback to encrypted disk)
            token_json = creds.to_json()
            saved_to_keyring = _save_token_to_keyring(token_json)
            if not saved_to_keyring:
                _save_token_to_disk(token_json)

        return creds

    def _service_client(self):
        if self._service is None:
            creds = self._get_credentials()
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)

        # FOR TESTING
        profile = (
            self._service.users()
            .getProfile(userId="me")
            .execute()
        )
        logger.info(
            "Authenticated Gmail account: %s",
            profile["emailAddress"],
        )
        return self._service

    # ── Email fetching ────────────────────────────────────────────────────────

    def fetch_emails_since(self, since: datetime) -> list[GmailMessage]:
        """
        Fetch both received (Primary) and sent emails after `since`.
        Returns all messages sorted oldest-first.
        
        Limits: Max 1000 emails per run to prevent OOM on large mailboxes.
        """
        epoch = int(since.timestamp())
        received = self._fetch_query(
            query=f"after:{epoch} category:primary",
            direction="received",
        )
        sent = self._fetch_query(
            query=f"after:{epoch} in:sent",
            direction="sent",
        )

        # Limit total emails to 1000 to prevent OOM
        max_total = 1000
        combined_count = len(received) + len(sent)
        if combined_count > max_total:
            logger.warning("Email count (%d) exceeds limit (%d); truncating", combined_count, max_total)
            # Keep more recent emails
            all_emails = received + sent
            all_emails.sort(key=lambda m: m.date, reverse=True)
            combined = all_emails[:max_total]
        else:
            # Deduplicate by message_id (a sent reply might appear in both)
            seen: set[str] = set()
            combined: list[GmailMessage] = []
            for msg in received + sent:
                if msg.message_id not in seen:
                    seen.add(msg.message_id)
                    combined.append(msg)

        # Sort oldest-first so Applied always precedes follow-up statuses
        combined.sort(key=lambda m: m.date)
        logger.info(
            "Total emails to process: %d (%d received, %d sent)",
            len(combined), len(received), len(sent),
        )
        return combined

    def _fetch_query(self, query: str, direction: str) -> list[GmailMessage]:
        """Fetch all messages matching a Gmail search query."""
        logger.info("Fetching Gmail messages with query: %s", query)
        try:
            service = self._service_client()
            messages_meta: list[dict] = []
            page_token: Optional[str] = None
            page_count = 0
            max_pages = 100  # Limit pagination to prevent runaway loops

            while page_count < max_pages:
                params: dict = {
                    "userId": "me",
                    "q": query,
                    "maxResults": GMAIL_MAX_RESULTS,
                }
                if page_token:
                    params["pageToken"] = page_token

                result = service.users().messages().list(**params).execute()
                batch = result.get("messages", [])
                messages_meta.extend(batch)
                logger.debug("Fetched page %d of %d messages", page_count + 1, len(batch))

                page_token = result.get("nextPageToken")
                if not page_token:
                    break
                
                page_count += 1

            logger.info("Found %d candidate %s emails across %d pages", len(messages_meta), direction, page_count)
            parsed: list[GmailMessage] = []

            for meta in messages_meta:
                try:
                    msg = self._fetch_message(service, meta["id"], direction)
                    if msg:
                        parsed.append(msg)
                except HttpError as exc:
                    logger.warning("Skipping message %s: %s", meta["id"], exc)

            return parsed

        except HttpError as exc:
            logger.error("Gmail API error: %s", exc)
            raise

    def _fetch_message(
        self, service, message_id: str, direction: str
    ) -> Optional[GmailMessage]:
        raw = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

        headers = {
            h["name"].lower(): h["value"]
            for h in raw.get("payload", {}).get("headers", [])
        }

        subject  = headers.get("subject", "(no subject)")
        sender   = headers.get("from", "")
        date_str = headers.get("date", "")

        try:
            parsed_dt = email_lib.utils.parsedate_to_datetime(date_str)
            # isoformat() WITHOUT astimezone() keeps the original timezone
            iso_date = parsed_dt.isoformat()
            # Extract YYYY-MM-DD from the email's native timezone
            applied_date = parsed_dt.date().isoformat()
            
            logger.debug(
                "Email date — raw: %s → parsed: %s → iso: %s → applied: %s",
                date_str, parsed_dt, iso_date, applied_date
            )
        except Exception as e:
            logger.warning("Could not parse date '%s': %s", date_str, e)
            now = datetime.now()
            applied_date = now.date().isoformat()
            iso_date = now.isoformat()

        body    = self._extract_body(raw.get("payload", {}))
        snippet = raw.get("snippet", "")

        return GmailMessage(
            message_id=message_id,
            subject=subject,
            sender=sender,
            date=iso_date,
            applied_date=applied_date,
            body=body,
            snippet=snippet,
            direction=direction,
        )

    # ── Body extraction ───────────────────────────────────────────────────────

    def _extract_body(self, payload: dict) -> str:
        mime_type = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data", "")
        parts     = payload.get("parts", [])

        if mime_type == "text/plain" and body_data:
            return self._decode_base64(body_data)

        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return self._decode_base64(data)

        for part in parts:
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    html = self._decode_base64(data)
                    return re.sub(r"<[^>]+>", " ", html)

        for part in parts:
            result = self._extract_body(part)
            if result:
                return result

        return ""

    @staticmethod
    def _decode_base64(data: str) -> str:
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""

    # ── Token management ──────────────────────────────────────────────────────

    def clear_token(self) -> bool:
        """Delete stored token (for re-authentication). Returns True if successful."""
        # Try to delete from both keyring and disk
        keyring_deleted = _delete_token_from_keyring()
        disk_deleted = False
        
        try:
            Path(self.token_path).unlink()
            disk_deleted = True
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Could not delete token file: %s", e)
        
        success = keyring_deleted or disk_deleted
        if success:
            logger.info("Token cleared")
        return success
