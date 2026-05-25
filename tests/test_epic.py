import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from pawchestrator.config import PipelineSettings, Settings
from pawchestrator.epic import run_epic


def test_run_epic_runs_sub_issues_sequentially_with_shared_group_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[tuple[str, str | None]] = []
    client = _FakeSubIssueClient(
        [
            {
                "number": 43,
                "title": "First child",
                "url": "https://github.com/owner/repo/issues/43",
            },
            {
                "number": 44,
                "title": "Second child",
                "url": "https://github.com/owner/repo/issues/44",
            },
        ]
    )
    _patch_client(monkeypatch, client)
    _patch_pipeline(monkeypatch, calls)

    result = asyncio.run(
        run_epic(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            progress=lambda _message: None,
            group_id="group-123",
        )
    )

    assert client.fetched is True
    assert calls == [
        ("https://github.com/owner/repo/issues/43", "group-123"),
        ("https://github.com/owner/repo/issues/44", "group-123"),
    ]
    assert result.group_id == "group-123"
    assert result.sub_runs[0].issue_number == 43
    assert result.sub_runs[0].run_id == "run-43"
    assert result.sub_runs[0].pr_url == "https://github.com/owner/repo/pull/43"
    assert result.sub_runs[1].issue_number == 44


def test_run_epic_stops_on_first_pipeline_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[tuple[str, str | None]] = []
    _patch_client(
        monkeypatch,
        _FakeSubIssueClient(
            [
                {"number": 43, "url": "https://github.com/owner/repo/issues/43"},
                {"number": 44, "url": "https://github.com/owner/repo/issues/44"},
                {"number": 45, "url": "https://github.com/owner/repo/issues/45"},
            ]
        ),
    )
    _patch_pipeline(monkeypatch, calls, failures={44})

    with pytest.raises(RuntimeError, match="pipeline failed for 44"):
        asyncio.run(
            run_epic(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
                progress=lambda _message: None,
                group_id="group-123",
            )
        )

    assert calls == [
        ("https://github.com/owner/repo/issues/43", "group-123"),
        ("https://github.com/owner/repo/issues/44", "group-123"),
    ]


def test_run_epic_continues_on_failure_when_fail_fast_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(epic_fail_fast=False),
    )
    calls: list[tuple[str, str | None]] = []
    progress: list[str] = []
    _patch_client(
        monkeypatch,
        _FakeSubIssueClient(
            [
                {"number": 43, "url": "https://github.com/owner/repo/issues/43"},
                {"number": 44, "url": "https://github.com/owner/repo/issues/44"},
                {"number": 45, "url": "https://github.com/owner/repo/issues/45"},
            ]
        ),
    )
    _patch_pipeline(monkeypatch, calls, failures={44})

    result = asyncio.run(
        run_epic(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            progress=progress.append,
            group_id="group-123",
        )
    )

    assert calls == [
        ("https://github.com/owner/repo/issues/43", "group-123"),
        ("https://github.com/owner/repo/issues/44", "group-123"),
        ("https://github.com/owner/repo/issues/45", "group-123"),
    ]
    assert [sub_run.issue_number for sub_run in result.sub_runs] == [43, 45]
    assert "[epic] sub-issue #44 failed; continuing" in progress


class _FakeSubIssueClient:
    def __init__(self, sub_issues: list[dict[str, object]]) -> None:
        self.sub_issues = sub_issues
        self.fetched = False

    async def fetch_sub_issues(self, reference):
        self.fetched = True
        return self.sub_issues


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeSubIssueClient) -> None:
    monkeypatch.setattr("pawchestrator.epic.get_gh_token", lambda: "token")
    monkeypatch.setattr("pawchestrator.epic.GitHubIssueClient", lambda _token: client)


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, str | None]],
    *,
    failures: set[int] | None = None,
) -> None:
    failing_numbers = failures or set()

    async def fake_run_pipeline(
        issue_url: str,
        settings: Settings,
        *,
        group_id: str | None = None,
        repo_path: Path | None = None,
        progress=print,
    ):
        calls.append((issue_url, group_id))
        issue_number = int(issue_url.rsplit("/", 1)[1])
        if issue_number in failing_numbers:
            raise RuntimeError(f"pipeline failed for {issue_number}")
        return SimpleNamespace(
            run_id=f"run-{issue_number}",
            pr_url=f"https://github.com/owner/repo/pull/{issue_number}",
        )

    monkeypatch.setattr("pawchestrator.epic.run_pipeline", fake_run_pipeline)
