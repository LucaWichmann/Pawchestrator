# ADR 0005: Panel alignment via computed DOM offset

**Status:** Accepted  
**Date:** 2026-05-25

## Context

The Pawchestrator panel is injected as a sibling of `IssueBody-module__outerContainer` in the GitHub issue page DOM. That container is a flex row: a 40px avatar on the left, then the bordered comment box on the right.

The panel inherits the full width of its parent and starts at the left edge — misaligned with the comment box it visually belongs below.

Two approaches were considered:

1. **Hardcoded margin-left** — apply a fixed pixel offset matching the avatar column width (≈48px at time of writing).
2. **Computed offset** — at inject time, measure `innerBox.getBoundingClientRect().left - outerContainer.getBoundingClientRect().left` where `innerBox = document.querySelector('[data-testid="issue-body"]')`, and apply the result as `margin-left`.

## Decision

Use the **computed offset** (option 2).

## Reasons

- GitHub's avatar column width is not a stable value. It has changed before and carries no documented contract.
- `[data-testid="issue-body"]` is a stable `data-testid` attribute — less likely to churn than a hashed module CSS class.
- The computation runs once per inject, which is already triggered by a MutationObserver debounce — no extra cost.
- Hardcoding 48px would silently misalign on any GitHub layout change, and no test would catch it.

## Consequences

- Panel alignment is always accurate to the actual rendered comment box, regardless of avatar size or GitHub layout changes.
- If GitHub removes `[data-testid="issue-body"]`, the offset falls back to 0 (no `margin-left` applied) rather than crashing — panel is unaligned but functional.
- A future dev encountering `getBoundingClientRect` should read this ADR before replacing it with a hardcoded value.
