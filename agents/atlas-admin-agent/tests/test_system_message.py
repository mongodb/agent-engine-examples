"""Tests for the system prompt contract.

The prompt is a behavioral contract with the LLM. We don't assert exact
wording, but we do assert that the critical safety clauses and pieces of
Atlas vocabulary are present. If someone removes or softens one of these
clauses we want the test to fail.
"""

from __future__ import annotations

from atlas_admin_agent import system_message


def _rendered() -> str:
    return system_message.SYSTEM_PROMPT.format(today="2025-05-09", atlas_org_id="org-x")


def test_prompt_declares_atlas_admin_role() -> None:
    content = _rendered().lower()
    assert "atlas" in content
    assert "admin" in content


def test_prompt_explains_project_group_terminology() -> None:
    # Atlas API uses `group` and `/groups/{id}` but users speak `project`.
    content = _rendered()
    assert "project" in content.lower()
    assert "group" in content.lower()


def test_prompt_requires_approval_for_mutations() -> None:
    content = _rendered().lower()
    assert "approval" in content or "approve" in content
    for verb in ("post", "put", "patch", "delete"):
        assert verb in content


def test_prompt_forbids_atlas_execute_without_approval() -> None:
    content = _rendered().lower()
    # The specific safety rule: don't call atlas_execute unless the previous
    # turn was an approval message.
    assert "atlas_execute" in content
    # Language must contain "never" or "only" to make the rule clear.
    assert "never" in content or "must not" in content or "forbid" in content


def test_prompt_calls_out_human_description_requirement() -> None:
    content = _rendered()
    assert "human_description" in content


def test_prompt_mentions_every_named_tool() -> None:
    # If you add a tool, update this list. Keeps the prompt and tools in sync.
    content = _rendered()
    for tool_name in (
        "list_projects",
        "list_clusters",
        "list_snapshots",
        "list_database_users",
        "list_network_access_entries",
        "list_alerts",
        "list_backup_restore_jobs",
        "atlas_describe_endpoint",
        "atlas_request",
        "atlas_execute",
        "run_snapshot_restore_test",
        "execute_snapshot_restore_test",
        "check_snapshot_restore_test",
    ):
        assert tool_name in content, f"system prompt is missing reference to {tool_name}"


def test_prompt_forbids_inventing_ids() -> None:
    content = _rendered().lower()
    assert "never invent" in content or "do not invent" in content or "discover" in content


def test_prompt_injects_today_and_org_id() -> None:
    assert "2025-05-09" in _rendered()
    assert "org-x" in _rendered()


def test_prompt_handles_unset_org_id_gracefully() -> None:
    rendered = system_message.SYSTEM_PROMPT.format(today="2025-05-09", atlas_org_id="<unset>")
    assert "<unset>" in rendered


def test_prompt_tells_model_to_discover_context_not_ask_user() -> None:
    # If the user leaves out a project/cluster name, the agent should call
    # the list tools itself rather than asking. This prevents annoying
    # clarifying questions for IDs that are trivially discoverable.
    content = _rendered().lower()
    assert "list_projects" in content
    # Look for the positive directive: discover first, ask only if ambiguous.
    assert "before asking" in content or "before ask" in content or "yourself" in content


def test_prompt_tells_model_to_describe_endpoint_before_mutations() -> None:
    content = _rendered()
    # Schema should be fetched from the OpenAPI spec at run time, not hardcoded.
    assert "atlas_describe_endpoint" in content
    # Explicit guidance about INVALID_ATTRIBUTE lookup-first is present.
    lower = content.lower()
    assert "invalid_attribute" in lower or "invalid attribute" in lower


def test_prompt_has_explicit_conformance_rule() -> None:
    # The prompt should contain a direct statement that requests must
    # conform to the OpenAPI spec, plus the specific sub-rules (field
    # names, required fields, query params, path template, nested shape).
    # This is the single highest-level rule that wires all the lower-level
    # guidance together.
    content = _rendered()
    lower = content.lower()

    # The core "must conform" statement.
    assert "must conform" in lower

    # Each conformance dimension is explicitly addressed.
    assert "required" in lower  # required fields must be present
    assert "parameters" in lower  # query parameters must match
    assert "path template" in lower or "{groupId}" in content  # path must match
    assert "nested" in lower or "nesting" in lower  # nested shape must match


def test_prompt_mandates_describe_before_writes() -> None:
    # The describe step must be framed as mandatory, not optional. In an
    # earlier iteration it was described with "Otherwise, consult the spec"
    # and the LLM skipped it.
    content = _rendered()
    lower = content.lower()
    # Look for unambiguous language that this is a required pre-flight step.
    assert "must" in lower
    # Forbidden v1 field names must be called out by name so the LLM doesn't
    # reach for them from training data.
    for forbidden in ("providerSettings", "numShards", "regionsConfig"):
        assert forbidden in content, f"prompt should explicitly forbid {forbidden}"


def test_prompt_does_not_describe_full_cluster_schema() -> None:
    # The prompt should NOT embed a complete v2 cluster body example — that
    # would drift. It MAY mention a few legacy field names as "don't use these"
    # counter-examples to steer the LLM away from training-data leaks.
    content = _rendered()
    # No full JSON cluster body.
    assert '"replicationSpecs": [' not in content
    assert '"regionConfigs": [' not in content
    # No "correct" legacy field in any positive example.
    assert "providerSettings: AWS" not in content
    # Explicit "do not include these" warning is expected and desirable.
    assert "INVALID_ATTRIBUTE" in content
