"""
Core Pydantic data models for the MCP drift study.

Three top-level entities:
  - ToolDefinition  : immutable snapshot of one tool at one commit
  - DriftEvent      : a detected change between two ToolDefinition snapshots
  - ServerSample    : one MCP server repo drawn from the sample frame
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class ChangeType(str, Enum):
    """
    Classification of how a tool definition changed between two commits.

    Values are intentionally ordered from least to most security-relevant.
    The LLM-jury classification step maps to one of these; human validation
    uses the same vocabulary for Kappa agreement calculations.
    """

    COSMETIC = "cosmetic"
    """Whitespace, punctuation, capitalisation — no semantic change."""

    SCHEMA_CHANGE = "schema_change"
    """input_schema properties/types/required fields altered."""

    PERMISSION_CHANGE = "permission_change"
    """permissions list added, removed, or modified."""

    DESCRIPTION_SEMANTIC_CHANGE = "description_semantic_change"
    """Description wording changed in a way that alters the tool's stated intent."""

    NAME_UNCHANGED_BEHAVIOR_CHANGED = "name_unchanged_behavior_changed"
    """
    The tool name is the same but the combined effect of all changes
    plausibly alters what an agent would do with this tool — the core
    "rug pull" signal we are hunting for.
    """


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class ToolDefinition(BaseModel):
    """
    Immutable snapshot of a single MCP tool definition at a specific commit.

    Frozen so that before/after pairs in DriftEvent cannot be accidentally
    mutated after construction.
    """

    tool_name: str
    description: str
    # Raw JSON Schema object as declared by the MCP server author.
    input_schema: dict
    # None means the field was absent or not extractable from this tool definition.
    permissions: Optional[list[str]] = None
    # Repo-relative path to the file that declares this tool (e.g. "src/server.py").
    source_file_path: str
    commit_sha: str
    commit_date: datetime
    server_repo_url: str

    model_config = {"frozen": True}

    @field_validator("commit_sha")
    @classmethod
    def commit_sha_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("commit_sha must not be empty or whitespace")
        return v


class DriftEvent(BaseModel):
    """
    A detected change in a tool's definition between two consecutive commits.

    Invariants enforced at construction time:
    - before.tool_name == after.tool_name  (we're tracking the same tool)
    - before.server_repo_url == after.server_repo_url  (same repo)
    - tool_name matches the shared tool name of before/after
    - 0.0 <= confidence <= 1.0
    """

    server_repo_url: str
    tool_name: str
    before: ToolDefinition
    after: ToolDefinition
    change_type: ChangeType
    # Identifies who/what produced this classification, e.g.:
    #   "llm-jury:claude-opus-4-8"  or  "human:annotator-1"
    classified_by: str
    confidence: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def snapshots_are_consistent(self) -> "DriftEvent":
        if self.before.tool_name != self.after.tool_name:
            raise ValueError(
                f"before.tool_name '{self.before.tool_name}' != "
                f"after.tool_name '{self.after.tool_name}'"
            )
        if self.before.server_repo_url != self.after.server_repo_url:
            raise ValueError(
                "before and after snapshots must come from the same repository"
            )
        if self.tool_name != self.before.tool_name:
            raise ValueError(
                f"DriftEvent.tool_name '{self.tool_name}' does not match "
                f"snapshot tool_name '{self.before.tool_name}'"
            )
        return self


class ServerSample(BaseModel):
    """
    One MCP server repository drawn from the sample frame.

    Optional fields reflect what different seed datasets provide:
    MCPCrawler supplies stars; MCP-at-First-Glance may not.
    """

    repo_url: str
    # Which dataset this server came from (e.g. "mcpcrawler-2024", "mcp-first-glance").
    registry_source: str
    category: Optional[str] = None
    language: Optional[str] = None
    stars: Optional[int] = Field(default=None, ge=0)
    last_commit_date: Optional[datetime] = None
