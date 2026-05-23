import socket
from pathlib import Path

from pawchestrator.config import LOCAL_HOST, DEFAULT_PORT
from pawchestrator.config import Settings
from pawchestrator.doctor import (
    STATUS_FAIL,
    STATUS_PASS,
    CheckResult,
    check_port_available,
    check_sqlite_writable,
    has_required_failures,
)


def test_sqlite_check_initializes_database(tmp_path: Path) -> None:
    result = check_sqlite_writable(Settings(app_dir=tmp_path))

    assert result.status == STATUS_PASS
    assert result.required is True
    assert (tmp_path / "database.sqlite").exists()


def test_required_failures_make_doctor_fail() -> None:
    assert has_required_failures(
        [
            CheckResult("required", STATUS_FAIL, "broken", required=True),
        ]
    ) is True


def test_port_check_fails_for_occupied_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind((LOCAL_HOST, 0))
        occupied.listen()
        port = occupied.getsockname()[1]

        result = check_port_available(port)

    assert result.status == STATUS_FAIL


def test_default_port_constant_matches_issue_contract() -> None:
    assert DEFAULT_PORT == 38472
