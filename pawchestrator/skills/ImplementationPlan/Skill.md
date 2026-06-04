You are creating an implementation plan for a GitHub issue.

→ a JSON object matching this schema exactly:
{
  "schema": "pawchestrator.implementation_plan.v1",
  "approach_summary": "string - one terse sentence ≤150 chars",
  "steps": [
    {
      "order": 1,
      "description": "string",
      "files_to_modify": ["path/to/file.py"],
      "notes": "string"
    }
  ],
  "file_operations": [
    {
      "path": "path/to/file.py",
      "type": "create" | "modify" | "delete",
      "description": "one-line summary, <=100 chars"
    }
  ],
  "files_to_modify": ["deduplicated list of all files"],
  "estimated_risk": "low" | "medium" | "high"
}

Use your Read, Glob, Grep tools to explore the codebase before planning.
No prose. No progress updates. Emit valid JSON artifact only.
Keep descriptions under 20 words per step.
Each file_operations.description must be one line and <=100 chars.
