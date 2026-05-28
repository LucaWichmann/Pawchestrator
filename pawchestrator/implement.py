"""Implementation stage orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.codegraph import CodeGraphSyncResult, seed_worktree_index, sync_back_if_merged
from pawchestrator.config import Settings
from pawchestrator.skill_loader import load_skill
from pawchestrator.db import (
    get_run_by_pr_number,
    get_run_state,
    insert_run_warning,
    lookup_repo_path,
    upsert_worktree_record,
)
from pawchestrator.github import GitHubIssueClient, get_gh_token
from pawchestrator.runners import (
    Runner,
    RunnerFailedError,
    RunnerTask,
    resolve_runner,
    resolve_repair_runner as resolve_configured_repair_runner,
    runner_tool_mismatch_warning,
)
from pawchestrator.stage_lifecycle import (
    StageFailedWithArtifact,
    StageResult,
    run_stage_lifecycle,
)

IMPLEMENTATION_REPORT_SCHEMA = "pawchestrator.implementation_report.v1"
REQUIRED_TOOLS: list[str] = [
    "Read",
    "Glob",
    "Grep",
    "Edit",
    "MultiEdit",
    "Write",
    "Bash",
]
DEFAULT_BASE_BRANCH = "main"
DEFAULT_REMOTE = "origin"
SLUG_MAX_LENGTH = 40
MAX_PROMPT_APPROACH_SUMMARY_CHARS = 150
REPAIR_REPORT_SCHEMA = "pawchestrator.repair_report.v1"
LOGGER = logging.getLogger(__name__)
NO_CHANGES_REQUESTED_REVIEWERS = "no_changes_requested_reviewers"


@dataclass(frozen=True)
class WorktreeInfo:
    path: Path
    branch: str
    reused: bool


@dataclass(frozen=True)
class RepairResult:
    run_id: str
    artifact_path: Path
    log_path: Path
    worktree_path: Path
    branch: str
    report: dict[str, Any]


async def run_implement(
    run_id: str,
    settings: Settings,
    *,
    repo_path: Path | None = None,
    runner: Runner | None = None,
    repair_context: dict[str, Any] | None = None,
    repair_attempt: int | None = None,
    allow_dirty_existing_worktree: bool = False,
    worktree_branch: str | None = None,
    worktree_path: Path | None = None,
    base_branch: str = DEFAULT_BASE_BRANCH,
) -> StageResult:
    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    source_repo_path = (repo_path or Path.cwd()).resolve()
    active_runner = runner or resolve_runner(settings, "implement", "codex")
    _log_tool_mismatch(active_runner)
    artifact_path = _implementation_report_path(settings, run_id)
    worktree_info: WorktreeInfo | None = None
    codegraph_messages: list[str] = []

    async def body(log_path: Path) -> tuple[dict[str, Any], Path]:
        nonlocal worktree_info, codegraph_messages
        snapshot_path = _snapshot_artifact_path(settings, run_id)
        if not snapshot_path.exists():
            raise FileNotFoundError(f"issue snapshot not found: {snapshot_path}")

        plan_path = _plan_artifact_path(settings, run_id)
        if not plan_path.exists() and repair_context is None:
            raise FileNotFoundError(f"implementation plan not found: {plan_path}")

        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        implementation_plan = (
            json.loads(plan_path.read_text(encoding="utf-8"))
            if plan_path.exists()
            else {}
        )
        worktree_kwargs: dict[str, Any] = {
            "snapshot": snapshot,
            "source_repo_path": source_repo_path,
            "allow_dirty_existing_worktree": allow_dirty_existing_worktree,
        }
        if worktree_branch is not None:
            worktree_kwargs["branch_override"] = worktree_branch
        if worktree_path is not None:
            worktree_kwargs["path_override"] = worktree_path
        if base_branch != DEFAULT_BASE_BRANCH:
            worktree_kwargs["base_branch"] = base_branch
        worktree_info = await ensure_issue_worktree(settings, **worktree_kwargs)
        await upsert_worktree_record(
            settings,
            run_id=run_id,
            owner=str(snapshot.get("owner") or state["owner"]),
            repo=str(snapshot.get("repo") or state["repo"]),
            issue_number=int(snapshot.get("number") or state["issue_number"]),
            branch=worktree_info.branch,
            path=worktree_info.path,
        )
        codegraph_messages.extend(
            await _sync_codegraph_for_implement(
                settings,
                source_repo_path=source_repo_path,
                worktree_path=worktree_info.path,
                branch=worktree_info.branch,
            )
        )

        healthy, message = await active_runner.check_health()
        if not healthy:
            raise RuntimeError(message)

        base_commit = await _git_rev_parse_head(worktree_info.path)
        base_dirty_diff = ""
        if allow_dirty_existing_worktree:
            base_dirty_diff = await _diff_since(worktree_info.path, base_commit)
        result = await active_runner.run_task(
            RunnerTask(
                prompt=build_implement_prompt(
                    snapshot,
                    implementation_plan,
                    worktree_info.path,
                    run_id=run_id,
                    repair_context=repair_context,
                    repair_attempt=repair_attempt,
                    app_dir=settings.app_dir,
                ),
                cwd=worktree_info.path,
                run_id=run_id,
                stage_name="implement",
            )
        )
        _write_implement_log(
            log_path,
            result.stdout,
            _codegraph_stderr(codegraph_messages, result.stderr),
        )

        end_commit = await _git_rev_parse_head(worktree_info.path)
        diff = await _diff_since(worktree_info.path, base_commit)
        no_dirty_delta = (
            allow_dirty_existing_worktree
            and end_commit == base_commit
            and diff == base_dirty_diff
        )
        if no_dirty_delta:
            diff = ""
        if not diff.strip() and not no_dirty_delta:
            diff = result.diff
        if not diff.strip():
            diff = await _committed_diff_against_base(worktree_info.path, base_branch)
        files_changed = files_changed_from_diff(diff)
        no_changes_error = _no_changes_error(
            exit_code=result.exit_code,
            files_changed=files_changed,
            implementation_plan=implementation_plan,
        )
        report = build_implementation_report(
            status="error" if no_changes_error or result.exit_code != 0 else "success",
            files_changed=files_changed,
            diff=diff,
            stdout=result.stdout,
            stderr=result.stderr,
            error=no_changes_error,
        )

        if no_changes_error:
            raise StageFailedWithArtifact(no_changes_error, report, artifact_path)

        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "Codex runner failed"
            report["error"] = detail
            report["status"] = "error"
            _write_report(artifact_path, report)
            raise RunnerFailedError(
                public_message=f"Runner exited with code {result.exit_code}",
                exit_code=result.exit_code,
                stderr=result.stderr,
                stdout=result.stdout,
            )

        report["worktree_path"] = str(worktree_info.path)
        report["branch"] = worktree_info.branch
        return report, artifact_path

    try:
        return await run_stage_lifecycle(settings, run_id, "implement", body)
    except Exception as error:
        if not artifact_path.exists():
            report = build_implementation_report(
                status="error",
                files_changed=[],
                diff="",
                stdout="",
                stderr="",
                error=str(error),
            )
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        raise


def _log_tool_mismatch(runner: Runner) -> None:
    warning = runner_tool_mismatch_warning(
        runner,
        stage_name="implement",
        required_tools=REQUIRED_TOOLS,
    )
    if warning is not None:
        LOGGER.warning(warning)


async def _sync_codegraph_for_implement(
    settings: Settings,
    *,
    source_repo_path: Path,
    worktree_path: Path,
    branch: str,
) -> list[str]:
    messages: list[str] = []
    try:
        sync_back_result = await sync_back_if_merged(
            settings,
            source_repo_path=source_repo_path,
            worktree_path=worktree_path,
            branch=branch,
        )
    except Exception as error:
        messages.append(f"[codegraph] sync-back warning: {error}")
    else:
        messages.append(_format_codegraph_result("sync-back", sync_back_result))

    try:
        seed_result = await seed_worktree_index(
            settings,
            source_repo_path=source_repo_path,
            worktree_path=worktree_path,
        )
    except Exception as error:
        messages.append(f"[codegraph] seed warning: {error}")
    else:
        messages.append(_format_codegraph_result("seed", seed_result))
    return messages


def _format_codegraph_result(label: str, result: CodeGraphSyncResult) -> str:
    return f"[codegraph] {label} {result.action}: {result.message}"


def _codegraph_stderr(messages: list[str], stderr: str) -> str:
    if not messages:
        return stderr
    sync_log = "\n".join(messages)
    if not stderr:
        return f"{sync_log}\n"
    return f"{sync_log}\n{stderr}"


async def ensure_issue_worktree(
    settings: Settings,
    *,
    snapshot: dict[str, Any],
    source_repo_path: Path,
    allow_dirty_existing_worktree: bool = False,
    branch_override: str | None = None,
    path_override: Path | None = None,
    base_branch: str = DEFAULT_BASE_BRANCH,
) -> WorktreeInfo:
    owner = str(snapshot.get("owner") or "")
    repo = str(snapshot.get("repo") or "")
    number = int(snapshot.get("number") or 0)
    title = str(snapshot.get("title") or "")
    branch = branch_override or f"paw/issue-{number}-{slugify(title)}"
    path = path_override or settings.app_dir / "worktrees" / owner / repo / f"issue-{number}"
    resolved_base_branch = base_branch or DEFAULT_BASE_BRANCH

    if path.exists():
        if (path / ".git").exists():
            if allow_dirty_existing_worktree:
                return WorktreeInfo(path=path, branch=branch, reused=True)
            await _prepare_base_branch(source_repo_path, resolved_base_branch)
            await _ensure_clean_worktree(path, "issue worktree")
            await _run_git_checked(["merge", "--ff-only", resolved_base_branch], path)
            return WorktreeInfo(path=path, branch=branch, reused=True)
        raise RuntimeError(f"worktree path exists but is not a git worktree: {path}")

    await _prepare_base_branch(source_repo_path, resolved_base_branch)

    path.parent.mkdir(parents=True, exist_ok=True)
    branch_exists = await _git_branch_exists(source_repo_path, branch)
    if branch_exists:
        await _run_git_checked(["worktree", "add", str(path), branch], source_repo_path)
    else:
        await _run_git_checked(
            ["worktree", "add", "-b", branch, str(path), resolved_base_branch],
            source_repo_path,
        )
    return WorktreeInfo(path=path, branch=branch, reused=False)


async def ensure_pr_worktree(
    settings: Settings,
    *,
    source_repo_path: Path,
    owner: str,
    repo: str,
    pr_number: int,
    head_branch: str,
    remote: str = DEFAULT_REMOTE,
) -> WorktreeInfo:
    branch_ref = head_branch.split(":", 1)[-1]
    suffix = uuid_slug()
    branch = f"paw/repair-pr-{pr_number}-{suffix}"
    path = settings.app_dir / "worktrees" / owner / repo / f"repair-{pr_number}-{suffix}"
    if path.exists():
        raise RuntimeError(f"repair worktree path already exists: {path}")

    await _run_git_checked(
        ["fetch", remote, f"+refs/heads/{branch_ref}:refs/remotes/{remote}/{branch_ref}"],
        source_repo_path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    await _run_git_checked(
        [
            "worktree",
            "add",
            "-b",
            branch,
            str(path),
            f"refs/remotes/{remote}/{branch_ref}",
        ],
        source_repo_path,
    )
    return WorktreeInfo(path=path, branch=branch, reused=False)


def uuid_slug() -> str:
    from uuid import uuid4

    return str(uuid4())[:8]


async def resolve_repair_runner(
    settings: Settings,
    *,
    owner: str,
    repo: str,
    pr_number: int,
) -> Runner:
    original_run = await get_run_by_pr_number(
        settings,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
    )
    implement_runner = None
    if original_run is not None:
        candidate = original_run.get("implement_runner") or original_run.get("runner")
        implement_runner = str(candidate) if candidate else None
    return await resolve_configured_repair_runner(settings, implement_runner)


async def run_repair(
    run_id: str,
    settings: Settings,
    *,
    client: GitHubIssueClient | None = None,
    runner: Runner | None = None,
) -> RepairResult:
    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    artifact_path = _repair_report_path(settings, run_id)
    owner = str(state["owner"])
    repo = str(state["repo"])
    pr_number = int(state["pr_number"])
    github_client = client or GitHubIssueClient(get_gh_token())
    worktree_info: WorktreeInfo | None = None
    report: dict[str, Any] = {}

    async def body(log_path: Path) -> tuple[dict[str, Any], Path]:
        nonlocal worktree_info, report
        repo_path = await lookup_repo_path(settings, owner=owner, repo=repo)
        if repo_path is None:
            raise RuntimeError(
                "repo not registered - run `pawchestrator repo add <path>` first"
            )
        head_branch, comments, diff = await asyncio.gather(
            github_client.fetch_pr_head_branch(owner, repo, pr_number),
            github_client.fetch_review_comments(owner, repo, pr_number),
            github_client.fetch_pr_diff(owner, repo, pr_number),
        )
        worktree_info = await ensure_pr_worktree(
            settings,
            source_repo_path=repo_path,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_branch=head_branch,
        )
        active_runner = runner or await resolve_repair_runner(
            settings,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
        )
        healthy, message = await active_runner.check_health()
        if not healthy:
            raise RuntimeError(message)

        base_commit = await _git_rev_parse_head(worktree_info.path)
        result = await active_runner.run_task(
            RunnerTask(
                prompt=build_repair_prompt(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    worktree_path=worktree_info.path,
                    review_comments=comments,
                    diff=diff,
                ),
                cwd=worktree_info.path,
                run_id=run_id,
                stage_name="repair",
            )
        )
        _write_implement_log(log_path, result.stdout, result.stderr)
        diff_after = await _diff_since(worktree_info.path, base_commit)
        if not diff_after.strip():
            diff_after = result.diff
        files_changed = files_changed_from_diff(diff_after)
        report = {
            "schema": REPAIR_REPORT_SCHEMA,
            "status": "error" if result.exit_code != 0 else "success",
            "head_branch": head_branch,
            "files_changed": files_changed,
            "diff_summary": summarize_diff(files_changed, diff_after),
            "codex_output": f"{result.stdout}{result.stderr}",
            "error": None,
        }
        _write_report(artifact_path, report)
        if result.exit_code != 0:
            raise RunnerFailedError(
                public_message=f"Repair agent exited with code {result.exit_code}",
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return report, artifact_path

    try:
        repair_stage = await run_stage_lifecycle(settings, run_id, "repair", body)
    except Exception as error:
        if not artifact_path.exists():
            _write_report(
                artifact_path,
                {
                    "schema": REPAIR_REPORT_SCHEMA,
                    "status": "error",
                    "files_changed": [],
                    "diff_summary": "0 files changed",
                    "codex_output": "",
                    "error": str(error),
                },
            )
        raise

    if worktree_info is None:
        raise RuntimeError("repair worktree was not created")
    await run_repair_push(
        run_id,
        settings,
        client=github_client,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        branch=worktree_info.branch,
        worktree_path=worktree_info.path,
    )

    return RepairResult(
        run_id=run_id,
        artifact_path=artifact_path,
        log_path=repair_stage.log_path,
        worktree_path=worktree_info.path,
        branch=worktree_info.branch,
        report=report,
    )


async def run_repair_push(
    run_id: str,
    settings: Settings,
    *,
    client: GitHubIssueClient,
    owner: str,
    repo: str,
    pr_number: int,
    branch: str,
    worktree_path: Path,
) -> None:
    async def body(log_path: Path) -> tuple[dict[str, Any], None]:
        await _run_git_checked(["push", "origin", branch], worktree_path)
        reviewers = await client.fetch_changes_requested_reviewers(owner, repo, pr_number)
        if reviewers:
            await client.request_review(owner, repo, pr_number, reviewers)
        else:
            await insert_run_warning(
                settings,
                run_id=run_id,
                stage_name="push",
                code=NO_CHANGES_REQUESTED_REVIEWERS,
                message="No CHANGES_REQUESTED reviewers found; skipped re-review request.",
            )
        return {}, None

    await run_stage_lifecycle(settings, run_id, "push", body)


def build_repair_prompt(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    worktree_path: Path,
    review_comments: dict[str, list[dict[str, Any]]],
    diff: str,
) -> str:
    return f"""Repair pull request {owner}/{repo}#{pr_number}.

Working directory: {worktree_path}

Use the review comments and PR diff below to make the requested fixes. Commit the
fixes to the current worktree branch using conventional commits. Do not run build
or test commands; verification is handled separately.

Review comments:
{_prompt_json(review_comments)}

PR diff:
{diff}
"""


async def _prepare_base_branch(source_repo_path: Path, base_branch: str) -> None:
    if base_branch == DEFAULT_BASE_BRANCH:
        await _refresh_main_branch(source_repo_path)
        return
    if not await _git_branch_exists(source_repo_path, base_branch):
        raise RuntimeError(f"base branch not found: {base_branch}")


_IMPLEMENT_FALLBACK = "Implement the changes described in the plan. Make granular, well-named commits as you go. Commit message format: `type(scope): description` (conventional commits). Do not run build or test commands - verification is handled separately."
_REPAIR_VERIFICATION_FALLBACK = "Your task: verification or tests failed after implementation. Inspect the failure output and fix the failing tests or build errors. Do not re-implement the feature; only repair what is failing. Make focused commits using `type(scope): description`. Do not run build or test commands - verification is handled separately."


def build_implement_prompt(
    snapshot: dict[str, Any],
    implementation_plan: dict[str, Any],
    worktree_path: Path,
    *,
    run_id: str = "",
    repair_context: dict[str, Any] | None = None,
    repair_attempt: int | None = None,
    app_dir: Path | None = None,
) -> str:
    prompt_plan = _prompt_implementation_plan(implementation_plan)
    checkbox_section = _prompt_checkbox_criteria(snapshot, run_id)
    if repair_context is not None:
        instructions = (
            load_skill("RepairVerification", app_dir) or _REPAIR_VERIFICATION_FALLBACK
        )
        data_section = f"""Verification failure:
{_prompt_repair_context(repair_context, repair_attempt)}

Background (what was implemented):
Issue: #{snapshot.get("number")} - {snapshot.get("title", "")}
Repository: {snapshot.get("owner", "")}/{snapshot.get("repo", "")}
Working directory: {worktree_path}

Issue body:
{snapshot.get("body", "")}

Implementation plan:
{_prompt_json(prompt_plan)}
{checkbox_section}"""
        return f"{data_section}\n\n{instructions}"

    instructions = load_skill("WorkOnIssue", app_dir) or _IMPLEMENT_FALLBACK

    data_section = f"""Issue: #{snapshot.get("number")} - {snapshot.get("title", "")}
Repository: {snapshot.get("owner", "")}/{snapshot.get("repo", "")}
Working directory: {worktree_path}

Issue body:
{snapshot.get("body", "")}

IssueSnapshot JSON:
{_prompt_json(snapshot)}

Implementation plan:
{_prompt_json(prompt_plan)}
{checkbox_section}"""

    return f"{data_section}\n\n{instructions}"


def _prompt_checkbox_criteria(snapshot: dict[str, Any], run_id: str) -> str:
    checkboxes = _list_value(snapshot.get("checkboxes"))
    if not checkboxes:
        return ""

    issue_ref = (
        f"{snapshot.get('owner', '')}/{snapshot.get('repo', '')}/"
        f"{snapshot.get('number', '')}"
    )
    checkbox_command = f"pawchestrator checkbox check {issue_ref} <index>"
    if run_id:
        checkbox_command = f"{checkbox_command} --run-id {run_id}"

    lines = [
        "",
        "Acceptance criteria checkboxes — call "
        f"`{checkbox_command}` via Bash immediately after addressing each criterion:",
    ]
    for checkbox in checkboxes:
        if not isinstance(checkbox, dict):
            continue
        index = checkbox.get("index")
        text = str(checkbox.get("text") or "")
        lines.append(f"  {index}: {text}")
    return "\n".join(lines) + "\n"


def _prompt_repair_context(
    repair_context: dict[str, Any] | None,
    repair_attempt: int | None,
) -> str:
    if repair_context is None:
        return ""
    attempt = repair_attempt if repair_attempt is not None else 1
    return f"""Repair attempt: {attempt}
{_prompt_json(repair_context)}"""


def _prompt_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def _prompt_implementation_plan(implementation_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "approach_summary": str(implementation_plan.get("approach_summary") or "")[
            :MAX_PROMPT_APPROACH_SUMMARY_CHARS
        ],
        "steps": [
            _prompt_implementation_step(step)
            for step in _list_value(implementation_plan.get("steps"))
        ],
        "files_to_modify": _list_value(implementation_plan.get("files_to_modify")),
    }


def _prompt_implementation_step(step: object) -> dict[str, object]:
    if not isinstance(step, dict):
        return {"description": str(step), "files_to_modify": []}
    return {
        "description": str(step.get("description") or ""),
        "files_to_modify": _list_value(step.get("files_to_modify")),
    }


def _list_value(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def build_implementation_report(
    *,
    status: str,
    files_changed: list[str],
    diff: str,
    stdout: str,
    stderr: str,
    error: str | None,
) -> dict[str, Any]:
    return {
        "schema": IMPLEMENTATION_REPORT_SCHEMA,
        "status": status,
        "files_changed": files_changed,
        "diff_summary": summarize_diff(files_changed, diff),
        "codex_output": f"{stdout}{stderr}",
        "error": error,
    }


def _no_changes_error(
    *,
    exit_code: int,
    files_changed: list[str],
    implementation_plan: dict[str, Any],
) -> str | None:
    if exit_code != 0 or files_changed:
        return None
    if _plan_has_steps(implementation_plan):
        return "Codex completed without changing files"
    return None


def _plan_has_steps(implementation_plan: dict[str, Any]) -> bool:
    steps = implementation_plan.get("steps")
    return isinstance(steps, list) and len(steps) > 0


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:SLUG_MAX_LENGTH] or "issue"


def files_changed_from_diff(diff: str) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3]
        if path.startswith("b/"):
            path = path[2:]
        if path not in seen:
            seen.add(path)
            files.append(path)
    return files


def summarize_diff(files_changed: list[str], diff: str) -> str:
    if not diff.strip():
        return "0 files changed"
    count = len(files_changed)
    if count == 0:
        return "diff captured"
    names = ", ".join(files_changed[:5])
    suffix = "" if count <= 5 else f", and {count - 5} more"
    plural = "file" if count == 1 else "files"
    return f"{count} {plural} changed: {names}{suffix}"


async def _git_branch_exists(source_repo_path: Path, branch: str) -> bool:
    _, _, exit_code = await _run_git(
        ["rev-parse", "--verify", f"refs/heads/{branch}"],
        source_repo_path,
    )
    return exit_code == 0


async def _refresh_main_branch(source_repo_path: Path) -> None:
    remote_main = f"refs/remotes/{DEFAULT_REMOTE}/{DEFAULT_BASE_BRANCH}"
    await _run_git_checked(
        ["fetch", DEFAULT_REMOTE, f"{DEFAULT_BASE_BRANCH}:{remote_main}"],
        source_repo_path,
    )

    current_branch = (
        await _run_git_checked(["branch", "--show-current"], source_repo_path)
    ).strip()
    if current_branch == DEFAULT_BASE_BRANCH:
        await _ensure_clean_worktree(source_repo_path, "source repo main")
        await _run_git_checked(
            ["merge", "--ff-only", f"{DEFAULT_REMOTE}/{DEFAULT_BASE_BRANCH}"],
            source_repo_path,
        )
        return

    local_main = f"refs/heads/{DEFAULT_BASE_BRANCH}"
    if not await _git_ref_exists(source_repo_path, local_main):
        raise RuntimeError(f"local {DEFAULT_BASE_BRANCH} branch not found")
    if not await _git_is_ancestor(source_repo_path, local_main, remote_main):
        raise RuntimeError(
            f"local {DEFAULT_BASE_BRANCH} cannot fast-forward to "
            f"{DEFAULT_REMOTE}/{DEFAULT_BASE_BRANCH}; resolve divergent commits first"
        )
    await _run_git_checked(["update-ref", local_main, remote_main], source_repo_path)


async def _ensure_clean_worktree(cwd: Path, label: str) -> None:
    status = await _run_git_checked(["status", "--porcelain"], cwd)
    if status.strip():
        raise RuntimeError(f"{label} has uncommitted changes; clean or stash them first")


async def _git_ref_exists(source_repo_path: Path, ref: str) -> bool:
    _, _, exit_code = await _run_git(["rev-parse", "--verify", ref], source_repo_path)
    return exit_code == 0


async def _git_is_ancestor(source_repo_path: Path, ancestor: str, descendant: str) -> bool:
    stdout, stderr, exit_code = await _run_git(
        ["merge-base", "--is-ancestor", ancestor, descendant],
        source_repo_path,
    )
    if exit_code == 0:
        return True
    if exit_code == 1:
        return False
    detail = stderr.strip() or stdout.strip() or "git merge-base failed"
    raise RuntimeError(detail)


async def _run_git_checked(args: list[str], cwd: Path) -> str:
    stdout, stderr, exit_code = await _run_git(args, cwd)
    if exit_code != 0:
        detail = stderr.strip() or stdout.strip() or "git command failed"
        raise RuntimeError(detail)
    return stdout


async def _git_rev_parse_head(cwd: Path) -> str:
    return (await _run_git_checked(["rev-parse", "HEAD"], cwd)).strip()


async def _diff_since(cwd: Path, base_commit: str) -> str:
    stdout, _, exit_code = await _run_git(["diff", base_commit], cwd)
    if exit_code != 0:
        return ""
    return stdout


async def _committed_diff_against_base(cwd: Path, base_branch: str) -> str:
    stdout, _, exit_code = await _run_git(["diff", f"{base_branch}...HEAD"], cwd)
    if exit_code != 0:
        return ""
    return stdout


async def _run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    return stdout, stderr, proc.returncode or 0


def _snapshot_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "issue.snapshot.json"


def _plan_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "implementation_plan.json"


def _implementation_report_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "implementation_report.json"


def _implement_log_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "stdout" / "implement.log"


def _repair_report_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "repair_report.json"


def _repair_log_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "stdout" / "repair.log"


def _write_implement_log(log_path: Path, stdout: str, stderr: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"[stdout]\n{stdout}\n[stderr]\n{stderr}\n",
        encoding="utf-8",
    )


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
