from pawchestrator import stream_tokens


def test_mint_stream_token_stores_scoped_token(monkeypatch) -> None:
    stream_tokens._stream_tokens.clear()
    monkeypatch.setattr(stream_tokens.time, "time", lambda: 1000.0)

    token = stream_tokens.mint_stream_token("run-123")

    assert isinstance(token, str)
    assert stream_tokens._stream_tokens[token] == ("run-123", 1300.0)
    assert stream_tokens.validate_stream_token(token, "run-123") is True


def test_validate_stream_token_returns_false_and_deletes_expired_token(
    monkeypatch,
) -> None:
    stream_tokens._stream_tokens.clear()
    stream_tokens._stream_tokens["expired-token"] = ("run-123", 999.0)
    monkeypatch.setattr(stream_tokens.time, "time", lambda: 1000.0)

    assert stream_tokens.validate_stream_token("expired-token", "run-123") is False
    assert "expired-token" not in stream_tokens._stream_tokens


def test_validate_stream_token_returns_false_for_wrong_run_id(monkeypatch) -> None:
    stream_tokens._stream_tokens.clear()
    monkeypatch.setattr(stream_tokens.time, "time", lambda: 1000.0)
    stream_tokens._stream_tokens["stream-token"] = ("run-123", 1300.0)

    assert stream_tokens.validate_stream_token("stream-token", "run-456") is False
    assert stream_tokens._stream_tokens["stream-token"] == ("run-123", 1300.0)


def test_validate_stream_token_returns_false_for_unknown_token(monkeypatch) -> None:
    stream_tokens._stream_tokens.clear()
    monkeypatch.setattr(stream_tokens.time, "time", lambda: 1000.0)

    assert stream_tokens.validate_stream_token("unknown-token", "run-123") is False
