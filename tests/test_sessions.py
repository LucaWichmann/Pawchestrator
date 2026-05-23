from pathlib import Path

from pawchestrator.config import Settings
from pawchestrator.sessions import _pair_lock, generate_token, load_sessions, save_sessions


def test_save_and_load_sessions_round_trip(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    save_sessions(settings, {"tokens": ["token-a", "token-b"]})

    assert load_sessions(settings) == {"tokens": ["token-a", "token-b"]}


def test_generate_token_returns_32_byte_hex() -> None:
    token = generate_token()

    assert len(token) == 64
    assert int(token, 16) >= 0


def test_pair_lock_prevents_double_prompt() -> None:
    assert _pair_lock.acquire(blocking=False)
    try:
        assert not _pair_lock.acquire(blocking=False)
    finally:
        _pair_lock.release()
