You are creating a minimal implementation plan for a trivial GitHub issue.

-> a JSON object matching this schema exactly:
{
  "schema": "pawchestrator.micro_plan.v1",
  "approach_summary": "string - one sentence <=100 chars",
  "steps": ["string - max 3 items, each <=15 words"],
  "files_to_modify": ["path/to/file.py"]
}

No prose. No progress updates. Emit valid JSON artifact only.
Max 3 steps. No file_operations, no estimated_risk, no notes.
