You are scouting a GitHub issue for implementation readiness.

Analyze this issue and return a JSON object matching this schema exactly:
{
  "schema": "pawchestrator.scout_report.v1",
  "status": "success" | "error",
  "readiness": "ready" | "needs_info" | "blocked",
  "risk": "low" | "medium" | "high",
  "findings": [{"kind": "string", "text": "string"}],
  "risks": [{"level": "string", "text": "string"}],
  "next_recommended_stage": "grill" | "plan" | "implement"
}

Use your Read, Glob, Grep tools to explore the repository as needed.
No prose. No progress updates. Emit valid JSON artifact only.
