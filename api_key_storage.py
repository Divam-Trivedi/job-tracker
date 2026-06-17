"""Secure API key storage using OS keyring + encrypted file fallback."""

from pathlib import Path
from typing import Optional
import logging
import config as cfg

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "job-tracker"
_KEYRING_USER_APIKEY = "llm-api-key"


def _try_import_keyring():
    """Try to import keyring. Return module or None if unavailable."""
    try:
        import keyring
        return keyring
    except ImportError:
        return None


def get_api_key() -> Optional[str]:
    """Retrieve API key from keyring or encrypted file. Returns None if not found."""
    keyring = _try_import_keyring()
    
    # Try keyring first
    if keyring:
        try:
            key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER_APIKEY)
            if key:
                logger.debug("API key retrieved from keyring")
                return key
        except Exception as e:
            logger.debug("Keyring retrieval failed: %s", e)
    
    # Fallback to encrypted file
    return _get_from_encrypted_file()


def save_api_key(api_key: str) -> bool:
    """Save API key to keyring, with encrypted file fallback. Returns True if successful."""
    keyring = _try_import_keyring()
    
    if keyring:
        try:
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER_APIKEY, api_key)
            logger.info("API key saved to system keyring")
            return True
        except Exception as e:
            logger.warning("Keyring save failed: %s", e)
    
    # Fallback to encrypted file
    return _save_to_encrypted_file(api_key)


def delete_api_key() -> bool:
    """Delete API key from storage. Returns True if successful."""
    keyring = _try_import_keyring()
    success = False
    
    if keyring:
        try:
            keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USER_APIKEY)
            logger.info("API key deleted from keyring")
            success = True
        except Exception as e:
            logger.debug("Keyring delete failed: %s (may not exist)", e)
    
    # Also delete from file
    try:
        Path(cfg.API_KEY_PATH).unlink(missing_ok=True)
        logger.info("API key file deleted")
        success = True
    except Exception as e:
        logger.debug("File delete failed: %s", e)
    
    return success


# ──────────────────────────────────────────────────────────────────────────────
# Encrypted file fallback
# ──────────────────────────────────────────────────────────────────────────────

def _get_from_encrypted_file() -> Optional[str]:
    """Read API key from encrypted file. Returns None if not found or decryption fails."""
    path = Path(cfg.API_KEY_PATH)
    
    if not path.exists():
        return None
    
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
        import base64
        
        # Derive deterministic key from fixed salt
        # This allows decryption across app restarts
        salt = b"job-tracker-salt"
        kdf = PBKDF2(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        # Use a fixed passphrase derived from the salt itself
        key = base64.urlsafe_b64encode(
            kdf.derive(b"job-tracker-api-key-encryption")
        )
        cipher = Fernet(key)
        
        encrypted = path.read_bytes()
        decrypted = cipher.decrypt(encrypted).decode('utf-8')
        logger.debug("API key retrieved from encrypted file")
        return decrypted
    except Exception as e:
        logger.warning("Could not decrypt API key file: %s", e)
        return None


def _save_to_encrypted_file(api_key: str) -> bool:
    """Save API key to encrypted file. Returns True if successful."""
    path = Path(cfg.API_KEY_PATH)
    
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
        import base64
        
        # Derive same deterministic key as in _get_from_encrypted_file()
        salt = b"job-tracker-salt"
        kdf = PBKDF2(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(
            kdf.derive(b"job-tracker-api-key-encryption")
        )
        cipher = Fernet(key)
        
        encrypted = cipher.encrypt(api_key.encode('utf-8'))
        path.write_bytes(encrypted)
        path.chmod(0o600)  # Read-only by owner
        logger.info("API key saved to encrypted file")
        return True
    except Exception as e:
        logger.error("Could not save API key to file: %s", e)
        return False