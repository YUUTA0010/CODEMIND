"""Pydantic schemas for model output. Anything that fails validation is rejected, never partially trusted."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CitationIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @field_validator("end_line")
    @classmethod
    def _order(cls, v, info):
        if "start_line" in info.data and v < info.data["start_line"]:
            raise ValueError("end_line < start_line")
        return v


class HypothesisIn(BaseModel):
    statement: str = Field(min_length=3, max_length=2000)
    supporting: list[str] = Field(default_factory=list, max_length=10)
    contradicting: list[str] = Field(default_factory=list, max_length=10)
    citations: list[CitationIn] = Field(default_factory=list, max_length=10)


class PatchIn(BaseModel):
    diff: str = Field(min_length=1, max_length=200_000)
    rationale: str = Field(default="", max_length=4000)
    behavior_before: str = Field(default="", max_length=2000)
    behavior_after: str = Field(default="", max_length=2000)


class AgentTurn(BaseModel):
    action: Literal["retrieve", "final"]
    retrieve_query: str | None = Field(default=None, max_length=2000)
    hypotheses: list[HypothesisIn] = Field(default_factory=list, max_length=6)
    summary: str = Field(default="", max_length=4000)
    patch: PatchIn | None = None
    affected_tests: list[str] = Field(default_factory=list, max_length=20)
    unverified: list[str] = Field(default_factory=list, max_length=20)
