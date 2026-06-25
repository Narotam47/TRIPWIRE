"""
Unit tests for src/schemas.py.

Coverage targets:
- Happy-path construction for all three models
- Every required field: missing → ValidationError
- Type coercion Pydantic *should* do (e.g. ISO string → datetime)
- Type violations Pydantic must reject (e.g. str where dict is required)
- Numeric bounds: confidence ∈ [0, 1], stars ≥ 0
- Enum: all valid ChangeType values accepted; unknown string rejected
- Cross-field invariants on DriftEvent (tool name consistency, repo consistency)
- Immutability of ToolDefinition (frozen model)
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.schemas import ChangeType, DriftEvent, ServerSample, ToolDefinition


# --------------------------------------------------------------------------- #
# Shared fixtures
# --------------------------------------------------------------------------- #

REPO_URL = "https://github.com/example/mcp-payments"


@pytest.fixture
def base_tool_kwargs():
    return {
        "tool_name": "send_payment",
        "description": "Send a payment to a recipient.",
        "input_schema": {
            "type": "object",
            "properties": {"amount": {"type": "number"}, "recipient": {"type": "string"}},
            "required": ["amount", "recipient"],
        },
        "source_file_path": "src/server.py",
        "commit_sha": "abc1234def5678",
        "commit_date": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "server_repo_url": REPO_URL,
    }


@pytest.fixture
def tool_before(base_tool_kwargs):
    return ToolDefinition(**base_tool_kwargs)


@pytest.fixture
def tool_after(base_tool_kwargs):
    """Same tool, later commit — description has a semantic change."""
    return ToolDefinition(
        **{
            **base_tool_kwargs,
            "description": "Send a payment to a recipient. WARNING: this action is irreversible.",
            "commit_sha": "def5678abc1234",
            "commit_date": datetime(2024, 6, 1, tzinfo=timezone.utc),
        }
    )


def make_drift_event(tool_before, tool_after, **overrides) -> DriftEvent:
    defaults = {
        "server_repo_url": REPO_URL,
        "tool_name": "send_payment",
        "before": tool_before,
        "after": tool_after,
        "change_type": ChangeType.DESCRIPTION_SEMANTIC_CHANGE,
        "classified_by": "llm-jury:claude-opus-4-8",
        "confidence": 0.92,
    }
    return DriftEvent(**{**defaults, **overrides})


# --------------------------------------------------------------------------- #
# ToolDefinition — happy paths
# --------------------------------------------------------------------------- #


class TestToolDefinitionValid:
    def test_minimal_required_fields(self, base_tool_kwargs):
        tool = ToolDefinition(**base_tool_kwargs)
        assert tool.tool_name == "send_payment"
        assert tool.permissions is None  # optional, absent → None

    def test_permissions_stored(self, base_tool_kwargs):
        tool = ToolDefinition(**base_tool_kwargs, permissions=["read:balance", "write:transfer"])
        assert tool.permissions == ["read:balance", "write:transfer"]

    def test_empty_permissions_list(self, base_tool_kwargs):
        # Empty list is distinct from None (field present but empty vs. not extractable).
        tool = ToolDefinition(**base_tool_kwargs, permissions=[])
        assert tool.permissions == []

    def test_iso_string_coerced_to_datetime(self, base_tool_kwargs):
        base_tool_kwargs["commit_date"] = "2024-01-01T00:00:00Z"
        tool = ToolDefinition(**base_tool_kwargs)
        assert isinstance(tool.commit_date, datetime)

    def test_nested_input_schema_accepted(self, base_tool_kwargs):
        base_tool_kwargs["input_schema"] = {
            "type": "object",
            "properties": {"nested": {"type": "object", "properties": {"x": {"type": "number"}}}},
        }
        tool = ToolDefinition(**base_tool_kwargs)
        assert "nested" in tool.input_schema["properties"]


# --------------------------------------------------------------------------- #
# ToolDefinition — validation failures
# --------------------------------------------------------------------------- #


class TestToolDefinitionInvalid:
    @pytest.mark.parametrize("field", [
        "tool_name", "description", "input_schema",
        "source_file_path", "commit_sha", "commit_date", "server_repo_url",
    ])
    def test_missing_required_field_raises(self, base_tool_kwargs, field):
        del base_tool_kwargs[field]
        with pytest.raises(ValidationError) as exc_info:
            ToolDefinition(**base_tool_kwargs)
        assert field in str(exc_info.value)

    def test_empty_commit_sha_rejected(self, base_tool_kwargs):
        base_tool_kwargs["commit_sha"] = "   "
        with pytest.raises(ValidationError) as exc_info:
            ToolDefinition(**base_tool_kwargs)
        assert "commit_sha" in str(exc_info.value)

    def test_input_schema_string_rejected(self, base_tool_kwargs):
        base_tool_kwargs["input_schema"] = '{"type": "object"}'  # JSON string, not dict
        with pytest.raises(ValidationError):
            ToolDefinition(**base_tool_kwargs)

    def test_input_schema_list_rejected(self, base_tool_kwargs):
        base_tool_kwargs["input_schema"] = ["type", "object"]
        with pytest.raises(ValidationError):
            ToolDefinition(**base_tool_kwargs)

    def test_frozen_rejects_attribute_assignment(self, tool_before):
        with pytest.raises(Exception):  # pydantic raises ValidationError (frozen)
            tool_before.tool_name = "mutated_name"

    def test_frozen_rejects_dict_mutation_on_model(self, tool_before):
        # model_copy() returns a new instance; the original must be unchanged.
        modified = tool_before.model_copy(update={"description": "changed"})
        assert tool_before.description != modified.description


# --------------------------------------------------------------------------- #
# DriftEvent — happy paths
# --------------------------------------------------------------------------- #


class TestDriftEventValid:
    def test_basic_construction(self, tool_before, tool_after):
        event = make_drift_event(tool_before, tool_after)
        assert event.confidence == 0.92
        assert event.change_type == ChangeType.DESCRIPTION_SEMANTIC_CHANGE

    def test_confidence_boundary_zero(self, tool_before, tool_after):
        event = make_drift_event(tool_before, tool_after, confidence=0.0)
        assert event.confidence == 0.0

    def test_confidence_boundary_one(self, tool_before, tool_after):
        event = make_drift_event(tool_before, tool_after, confidence=1.0)
        assert event.confidence == 1.0

    def test_all_change_types_accepted(self, tool_before, tool_after):
        for ct in ChangeType:
            event = make_drift_event(tool_before, tool_after, change_type=ct)
            assert event.change_type == ct

    def test_change_type_from_string_value(self, tool_before, tool_after):
        event = make_drift_event(tool_before, tool_after, change_type="cosmetic")
        assert event.change_type == ChangeType.COSMETIC

    def test_classified_by_freeform_string(self, tool_before, tool_after):
        event = make_drift_event(tool_before, tool_after, classified_by="human:annotator-2")
        assert event.classified_by == "human:annotator-2"


# --------------------------------------------------------------------------- #
# DriftEvent — validation failures
# --------------------------------------------------------------------------- #


class TestDriftEventInvalid:
    def test_confidence_above_one_rejected(self, tool_before, tool_after):
        with pytest.raises(ValidationError) as exc_info:
            make_drift_event(tool_before, tool_after, confidence=1.001)
        assert "confidence" in str(exc_info.value)

    def test_confidence_below_zero_rejected(self, tool_before, tool_after):
        with pytest.raises(ValidationError) as exc_info:
            make_drift_event(tool_before, tool_after, confidence=-0.001)
        assert "confidence" in str(exc_info.value)

    def test_unknown_change_type_rejected(self, tool_before, tool_after):
        with pytest.raises(ValidationError):
            make_drift_event(tool_before, tool_after, change_type="silent_exfiltration")

    def test_mismatched_tool_names_rejected(self, base_tool_kwargs):
        before = ToolDefinition(**base_tool_kwargs)
        after = ToolDefinition(**{
            **base_tool_kwargs,
            "tool_name": "receive_payment",  # different tool name
            "commit_sha": "zzz9999",
        })
        with pytest.raises(ValidationError) as exc_info:
            DriftEvent(
                server_repo_url=REPO_URL,
                tool_name="send_payment",
                before=before,
                after=after,
                change_type=ChangeType.COSMETIC,
                classified_by="human:annotator-1",
                confidence=1.0,
            )
        assert "tool_name" in str(exc_info.value)

    def test_mismatched_repo_urls_rejected(self, base_tool_kwargs):
        before = ToolDefinition(**base_tool_kwargs)
        after = ToolDefinition(**{
            **base_tool_kwargs,
            "server_repo_url": "https://github.com/other/repo",
            "commit_sha": "zzz9999",
        })
        with pytest.raises(ValidationError) as exc_info:
            DriftEvent(
                server_repo_url=REPO_URL,
                tool_name="send_payment",
                before=before,
                after=after,
                change_type=ChangeType.COSMETIC,
                classified_by="human:annotator-1",
                confidence=1.0,
            )
        assert "repository" in str(exc_info.value)

    def test_event_tool_name_mismatch_with_snapshots_rejected(self, tool_before, tool_after):
        # DriftEvent.tool_name must equal before/after tool_name
        with pytest.raises(ValidationError) as exc_info:
            make_drift_event(tool_before, tool_after, tool_name="wrong_name")
        assert "tool_name" in str(exc_info.value)

    @pytest.mark.parametrize("field", ["before", "after", "change_type", "classified_by", "confidence"])
    def test_missing_required_field_raises(self, tool_before, tool_after, field):
        kwargs = {
            "server_repo_url": REPO_URL,
            "tool_name": "send_payment",
            "before": tool_before,
            "after": tool_after,
            "change_type": ChangeType.COSMETIC,
            "classified_by": "human:annotator-1",
            "confidence": 0.9,
        }
        del kwargs[field]
        with pytest.raises(ValidationError):
            DriftEvent(**kwargs)


# --------------------------------------------------------------------------- #
# ServerSample — happy paths
# --------------------------------------------------------------------------- #


class TestServerSampleValid:
    def test_minimal(self):
        sample = ServerSample(repo_url=REPO_URL, registry_source="mcpcrawler-2024")
        assert sample.stars is None
        assert sample.category is None
        assert sample.language is None
        assert sample.last_commit_date is None

    def test_fully_populated(self):
        sample = ServerSample(
            repo_url=REPO_URL,
            registry_source="mcp-first-glance",
            category="payments",
            language="Python",
            stars=142,
            last_commit_date=datetime(2024, 9, 15, tzinfo=timezone.utc),
        )
        assert sample.stars == 142
        assert sample.category == "payments"

    def test_zero_stars_accepted(self):
        sample = ServerSample(repo_url=REPO_URL, registry_source="mcpcrawler-2024", stars=0)
        assert sample.stars == 0

    def test_iso_string_last_commit_date_coerced(self):
        sample = ServerSample(
            repo_url=REPO_URL,
            registry_source="mcpcrawler-2024",
            last_commit_date="2024-03-20T12:00:00Z",
        )
        assert isinstance(sample.last_commit_date, datetime)


# --------------------------------------------------------------------------- #
# ServerSample — validation failures
# --------------------------------------------------------------------------- #


class TestServerSampleInvalid:
    def test_missing_repo_url_rejected(self):
        with pytest.raises(ValidationError):
            ServerSample(registry_source="mcpcrawler-2024")

    def test_missing_registry_source_rejected(self):
        with pytest.raises(ValidationError):
            ServerSample(repo_url=REPO_URL)

    def test_negative_stars_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            ServerSample(repo_url=REPO_URL, registry_source="mcpcrawler-2024", stars=-1)
        assert "stars" in str(exc_info.value)

    def test_stars_string_rejected(self):
        with pytest.raises(ValidationError):
            ServerSample(repo_url=REPO_URL, registry_source="mcpcrawler-2024", stars="many")
