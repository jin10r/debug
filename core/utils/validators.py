"""
Reusable input validation primitives for sensitive modules.

Defense-in-depth: every user-facing entry point (API handlers, WebSocket
messages, Telegram initData) validates through these helpers before touching
business logic or the database.

Conventions:
  - Reject first, accept second: return None/False/raise on any anomaly.
  - Never trust raw user input — always strip, type-check, and bound-length.
  - Raise ValueError for invalid input (callers map to HTTP 400/403).
"""

import re
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_INIT_DATA_LEN = 4096
MAX_BOT_TOKEN_LEN = 256
MAX_BODY_BYTES = 1_048_576  # 1 MB — generous for GeoJSON event payloads
MAX_MEDIA_FILE_BYTES = 10 * 1024 * 1048  # 10 MB
MAX_JWT_LEN = 8192
MAX_LAYER_NAME_LEN = 32

# Telegram user IDs are integers (64-bit positive).
_VALID_USER_ID_RE = re.compile(r'^\d{1,19}$')
_VALID_LAYER_RE = re.compile(r'^[a-z_]{1,32}$')

# Bot token format: <digits>:<alphanumeric-with-colons-and-dashes>
# This is a basic sanity check — real security comes from HMAC verification.
_VALID_BOT_TOKEN_RE = re.compile(r'^[A-Za-z0-9_:_-]{5,256}$')


# ---------------------------------------------------------------------------
# String / integer validators
# ---------------------------------------------------------------------------

def validate_string(value, field_name: str, max_len: int, min_len: int = 1) -> str:
    """Validate a user-supplied string: type, strip, null-byte reject, length-bound.

    Args:
        value: Raw input (any type — only str is accepted).
        field_name: Used in error messages for debugging.
        max_len: Maximum allowed length after stripping.
        min_len: Minimum allowed length after stripping (default 1).

    Returns:
        The stripped string.

    Raises:
        ValueError: If the input is not a str, contains null bytes,
                    or fails length constraints.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if '\x00' in stripped:
        raise ValueError(f"{field_name} contains null bytes")
    if len(stripped) < min_len:
        raise ValueError(f"{field_name} must be at least {min_len} characters")
    if len(stripped) > max_len:
        raise ValueError(f"{field_name} exceeds maximum length of {max_len}")
    return stripped


def validate_int(value, field_name: str, min_val: int, max_val: int) -> int:
    """Validate an integer input with range bounds.

    Accepts int or a numeric string. Rejects floats with non-zero fractional part.

    Raises:
        ValueError: If the value cannot be parsed as an int or is out of range.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer, not boolean")
    if isinstance(value, float):
        if value != int(value):
            raise ValueError(f"{field_name} must be an integer, got float")
        value = int(value)
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value.strip())
        except (ValueError, TypeError):
            raise ValueError(f"{field_name} must be an integer, got '{value}'")
    else:
        raise ValueError(f"{field_name} must be an integer, got {type(value).__name__}")

    if parsed < min_val:
        raise ValueError(f"{field_name} must be >= {min_val}, got {parsed}")
    if parsed > max_val:
        raise ValueError(f"{field_name} must be <= {max_val}, got {parsed}")
    return parsed


# ---------------------------------------------------------------------------
# ISO-8601 timestamp validator
# ---------------------------------------------------------------------------

def validate_iso_timestamp(value, field_name: str = "timestamp") -> datetime:
    """Parse and validate an ISO-8601 timestamp string.

    Always returns a timezone-aware datetime (UTC if input had no tz).
    Rejects strings that are clearly not timestamps.

    Raises:
        ValueError: If the string cannot be parsed as a valid ISO-8601 datetime.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    ts = value.strip()
    if len(ts) < 10 or len(ts) > 40:
        raise ValueError(f"{field_name} has invalid length")
    if '\x00' in ts:
        raise ValueError(f"{field_name} contains null bytes")
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        raise ValueError(f"{field_name} is not a valid ISO-8601 datetime: '{ts[:20]}...'")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Layer validation (events filter)
# ---------------------------------------------------------------------------

def validate_layers(layers) -> Optional[list[str]]:
    """Validate a list of layer names against the canonical set.

    Accepts None (no filter) or a list of valid layer-name strings.
    Returns a validated list or None.
    """
    if layers is None:
        return None
    if not isinstance(layers, list):
        raise ValueError("layers must be a list of strings")
    if len(layers) > 16:
        raise ValueError("layers list exceeds maximum of 16 entries")

    from core.settings import DEFAULT_LAYER_KEYWORDS
    valid_set = set(DEFAULT_LAYER_KEYWORDS.keys())
    result = []
    for i, layer in enumerate(layers):
        if not isinstance(layer, str):
            raise ValueError(f"layers[{i}] must be a string")
        layer = layer.strip()
        if not layer:
            raise ValueError(f"layers[{i}] is empty")
        if not _VALID_LAYER_RE.match(layer):
            raise ValueError(f"layers[{i}] '{layer}' contains invalid characters")
        if layer not in valid_set:
            raise ValueError(f"layers[{i}] '{layer}' is not a valid layer")
        result.append(layer)
    return result


# ---------------------------------------------------------------------------
# Telegram initData validation
# ---------------------------------------------------------------------------

def validate_init_data_length(init_data: str) -> str:
    """Validate raw init_data string length and content before HMAC parsing."""
    if not isinstance(init_data, str):
        raise ValueError("init_data must be a string")
    if '\x00' in init_data:
        raise ValueError("init_data contains null bytes")
    if len(init_data) > MAX_INIT_DATA_LEN:
        raise ValueError(f"init_data exceeds maximum length of {MAX_INIT_DATA_LEN}")
    return init_data.strip()


def validate_bot_token(token: str) -> str:
    """Validate Telegram bot token format (sanity check only — real auth is HMAC)."""
    if not isinstance(token, str):
        raise ValueError("bot_token must be a string")
    if '\x00' in token:
        raise ValueError("bot_token contains null bytes")
    if len(token) > MAX_BOT_TOKEN_LEN:
        raise ValueError(f"bot_token exceeds maximum length of {MAX_BOT_TOKEN_LEN}")
    if not _VALID_BOT_TOKEN_RE.match(token):
        raise ValueError("bot_token has invalid format (must contain only alphanumeric, colons, underscores, hyphens)")
    return token


def validate_max_age_hours(max_age_hours: int) -> int:
    """Validate max_age_hours parameter (must be 1 <= value <= 720).

    Нижняя граница 1 (не 0): при max_age_hours=0 любой init_data считался
    бы немедленно истёкшим — это логически некорректно.
    """
    return validate_int(max_age_hours, "max_age_hours", 1, 720)


# ---------------------------------------------------------------------------
# JWT token validation
# ---------------------------------------------------------------------------

def validate_jwt_string(token: str) -> str:
    """Basic format validation for JWT tokens before cryptographic verification."""
    if not isinstance(token, str):
        raise ValueError("JWT token must be a string")
    if '\x00' in token:
        raise ValueError("JWT token contains null bytes")
    if len(token) > MAX_JWT_LEN:
        raise ValueError(f"JWT token exceeds maximum length of {MAX_JWT_LEN}")
    parts = token.strip().split('.')
    if len(parts) != 3:
        raise ValueError("JWT token must have 3 parts separated by dots")
    return token.strip()


def validate_telegram_user_id(user_id) -> int:
    """Validate a Telegram user ID for JWT 'sub' claim."""
    if isinstance(user_id, int):
        if user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        return user_id
    if isinstance(user_id, str):
        if not _VALID_USER_ID_RE.match(user_id):
            raise ValueError(f"user_id '{user_id}' is not a valid Telegram user ID")
        return int(user_id)
    raise ValueError(f"user_id must be int or string, got {type(user_id).__name__}")
