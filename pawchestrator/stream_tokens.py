"""Short-lived stream token store for SSE authentication."""

import secrets
import time


STREAM_TOKEN_TTL = 300
_stream_tokens: dict[str, tuple[str, float]] = {}


def mint_stream_token(run_id: str) -> str:
    """Generate and store a scoped stream token. Returns the token string."""
    token = secrets.token_urlsafe(32)
    _stream_tokens[token] = (run_id, time.time() + STREAM_TOKEN_TTL)
    return token


def validate_stream_token(token: str, run_id: str) -> bool:
    """Return True if token exists, is not expired, and matches run_id."""
    now = time.time()
    for stored_token, (_stored_run_id, expires_at) in list(_stream_tokens.items()):
        if expires_at <= now:
            del _stream_tokens[stored_token]

    stored = _stream_tokens.get(token)
    if stored is None:
        return False

    stored_run_id, _expires_at = stored
    return stored_run_id == run_id
