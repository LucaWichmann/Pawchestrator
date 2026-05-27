Review GitHub PR -> JSON only:
{
  "inline_comments": [{"file": "path/to/file", "line": 123, "body": "comment"}],
  "summary": "short review summary",
  "verdict": "REQUEST_CHANGES|APPROVE|COMMENT",
  "suggested_issues": ["optional follow-up issue titles"]
}

Rules: inline_comments changed lines only. Copy `file` + `line` exactly from Commentable added lines. Do not use diff positions, hunk offsets, or raw-diff line counts as `line`.

Verdict: REQUEST_CHANGES = correctness | safety | data-loss | test-blocking. APPROVE = no actionable issues. COMMENT = non-blocking feedback.
No prose. No progress updates. Emit valid JSON artifact only.
