"""Shared structured report models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EpicSubIssue(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    depends_on_indexes: list[int] = Field(default_factory=list)


class EpicArchitectPlan(BaseModel):
    epic_analysis: str = Field(min_length=1)
    sub_issues: list[EpicSubIssue] = Field(default_factory=list)
