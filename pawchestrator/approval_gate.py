"""In-process plan approval gate coordination."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

ApprovalDecision = Literal["approve", "reject", "abort"]


@dataclass
class ApprovalGate:
    event: asyncio.Event
    decision: ApprovalDecision | None = None


_approval_gates: dict[str, ApprovalGate] = {}


def register_approval_event(run_id: str) -> asyncio.Event:
    gate = ApprovalGate(event=asyncio.Event())
    _approval_gates[run_id] = gate
    return gate.event


def signal_approval(run_id: str, *, approved: bool) -> bool:
    gate = _approval_gates.get(run_id)
    if gate is None:
        return False
    gate.decision = "approve" if approved else "abort"
    gate.event.set()
    return True


def signal_approval_decision(run_id: str, decision: ApprovalDecision) -> bool:
    gate = _approval_gates.get(run_id)
    if gate is None:
        return False
    gate.decision = decision
    gate.event.set()
    return True


def approval_decision(run_id: str) -> ApprovalDecision | None:
    gate = _approval_gates.get(run_id)
    return None if gate is None else gate.decision


def clear_approval_event(run_id: str) -> None:
    _approval_gates.pop(run_id, None)


def has_approval_event(run_id: str) -> bool:
    return run_id in _approval_gates
