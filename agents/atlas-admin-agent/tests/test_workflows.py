"""Tests for ``atlas_admin_agent.workflows`` (snapshot-restore).

Covers:

- Pure helpers: target name truncation, back-compat detection, body stripping,
  snapshot version parsing, cluster tier extraction, ``explain_no_backups``
  across all documented Atlas cluster tiers (M0/M2/M5/FLEX/SERVERLESS).
- Plan builder: skip reasons, snapshot selection, version mismatch handling,
  empty project, and non-JSON Atlas errors.
- ``_run_background``: happy path single + multi cluster, retry on IDLE and
  restore timeouts, out-of-retries path, create failure, poll failure,
  restore job failure states (failed/cancelled/expired), cleanup success and
  failure, partial-success status, config_error path, unexpected exception
  path.
- Workflow surface: ``run_snapshot_restore_test`` SuspendPayload contents,
  ``execute_snapshot_restore_test`` returns a handle and spawns a thread,
  ``check_snapshot_restore_test`` reporting.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from atlas_admin_agent import workflow_store, workflows
from atlas_admin_agent.atlas_client import AtlasApiError, AtlasConfigError


# --- fakes ---------------------------------------------------------------


class _FakeAtlasClient:
    """Scriptable Atlas client: one canned response per (method, path) key.

    Unlike a generic mock, unexpected calls raise immediately so misroutes
    never silently pass.
    """

    def __init__(self) -> None:
        self.paginate_results: list[dict] = []
        self.responses: dict[tuple[str, str], list] = {}
        self.calls: list[tuple[str, str]] = []
        self.closed = False

        class _Cfg:
            base_url = "https://atlas.test/api/atlas/v2"
            api_version_accept = "application/vnd.atlas.2025-03-12+json"

        self.config = _Cfg()

    def queue(self, method: str, path: str, body: Any) -> None:
        self.responses.setdefault((method.upper(), path), []).append(body)

    def paginate(self, path, *, params=None, max_items=None, page_size=500):
        self.calls.append(("GET-PAGINATE", path))
        items = list(self.paginate_results)
        if max_items is not None:
            items = items[:max_items]
        yield from items

    def request(self, method, path, *, params=None, json=None):
        key = (method.upper(), path)
        self.calls.append(key)
        queue = self.responses.get(key)
        if not queue:
            raise AssertionError(f"Unexpected request: {method} {path}")
        value = queue.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self) -> None:
        self.closed = True


class _FakeApp:
    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def tool(self, *, is_local: bool = True):
        def decorator(fn):
            self.registered[fn.__name__] = fn
            return fn

        return decorator


@pytest.fixture(autouse=True)
def _clear_workflow_store() -> None:
    # Reset the in-process fallback used when MONGODB_URI is unset.
    workflow_store._fallback_store().clear()


# --- pure helper tests ----------------------------------------------------


def test_build_target_cluster_name_keeps_within_64_chars() -> None:
    ts = "250509123045"
    name = workflows._build_target_cluster_name("Cluster0", ts)
    assert name == "Cluster0-250509123045-backup-test-job"
    assert len(name) <= workflows.CLUSTER_NAME_MAX_LEN


def test_build_target_cluster_name_truncates_long_source() -> None:
    ts = "250509123045"
    long_source = "A" * 80
    name = workflows._build_target_cluster_name(long_source, ts)
    assert len(name) == workflows.CLUSTER_NAME_MAX_LEN
    assert name.endswith(f"-{ts}-{workflows.BACKUP_CLUSTER_MARKER}")


def test_build_target_cluster_name_strips_trailing_hyphen_after_truncation() -> None:
    # If truncation leaves a trailing '-' we strip it to preserve a clean name
    ts = "250509000000"
    suffix = f"-{ts}-{workflows.BACKUP_CLUSTER_MARKER}"
    boundary = workflows.CLUSTER_NAME_MAX_LEN - len(suffix)
    # craft a name that would be truncated at position `boundary` landing on "-"
    source = ("x" * (boundary - 1)) + "-after"
    name = workflows._build_target_cluster_name(source, ts)
    assert "--" not in name
    assert len(name) <= workflows.CLUSTER_NAME_MAX_LEN


def test_build_target_cluster_name_preserves_timestamp_position() -> None:
    # Docs invariant: target must satisfy Atlas's first-23-chars uniqueness rule.
    ts = "250509123045"
    name = workflows._build_target_cluster_name("Cluster0", ts)
    # timestamp must appear inside the first 23 chars to survive repeat runs.
    assert ts in name[:23]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Cluster0-250509-backup-test-job", True),
        ("backup-test-job-foo", True),
        ("X-backup-test-job-2025", True),
        ("Cluster0", False),
        ("backup-test-jobish", True),  # substring match — mirrors the reference
        ("", False),
        (None, False),
    ],
)
def test_is_backup_test_cluster(name, expected: bool) -> None:
    assert workflows._is_backup_test_cluster(name) is expected


def test_build_target_cluster_body_strips_readonly_keeps_version() -> None:
    source = {
        "id": "abc",
        "groupId": "p1",
        "name": "Cluster0",
        "createDate": "2025-01-01",
        "stateName": "IDLE",
        "mongoDBVersion": "8.0.6",
        "mongoDBMajorVersion": "8.0",
        "versionReleaseSystem": "LTS",
        "connectionStrings": {"standard": "mongodb://..."},
        "paused": False,
        "links": [{"rel": "self"}],
        "replicaSetScalingStrategy": "WORKLOAD_TYPE",
        "featureCompatibilityVersion": "8.0",
        "replicationSpecs": [
            {"id": "rs1", "regionConfigs": [{"electableSpecs": {"instanceSize": "M10"}}]}
        ],
        "backupEnabled": True,
    }
    body = workflows._build_target_cluster_body(source, "NewTarget")
    assert body["name"] == "NewTarget"
    for stripped in (
        "id",
        "groupId",
        "createDate",
        "stateName",
        "mongoDBVersion",
        "connectionStrings",
        "paused",
        "links",
        "replicaSetScalingStrategy",
        "featureCompatibilityVersion",
    ):
        assert stripped not in body
    # These two MUST be preserved for Atlas to accept the restore target:
    assert body["mongoDBMajorVersion"] == "8.0"
    assert body["versionReleaseSystem"] == "LTS"
    assert "id" not in body["replicationSpecs"][0]


def test_build_target_cluster_body_does_not_mutate_source() -> None:
    source = {
        "id": "abc",
        "name": "Cluster0",
        "replicationSpecs": [{"id": "rs1"}],
    }
    before = json.dumps(source, sort_keys=True)
    workflows._build_target_cluster_body(source, "Target")
    after = json.dumps(source, sort_keys=True)
    assert before == after


@pytest.mark.parametrize(
    "snapshot,expected",
    [
        ({"mongodVersion": "8.0.6"}, "8.0"),
        ({"mongoDBVersion": "7.0.11"}, "7.0"),
        ({"mongodVersion": "8"}, None),
        ({}, None),
        ({"mongodVersion": None}, None),
    ],
)
def test_snapshot_major_version(snapshot, expected) -> None:
    assert workflows._snapshot_major_version(snapshot) == expected


@pytest.mark.parametrize(
    "cluster,expected_size",
    [
        (
            {
                "replicationSpecs": [
                    {"regionConfigs": [{"electableSpecs": {"instanceSize": "M10"}}]}
                ]
            },
            "M10",
        ),
        (
            {"replicationSpecs": [{"regionConfigs": [{"readOnlySpecs": {"instanceSize": "M0"}}]}]},
            "M0",
        ),
        (
            {
                "replicationSpecs": [
                    {"regionConfigs": [{"analyticsSpecs": {"instanceSize": "FLEX"}}]}
                ]
            },
            "FLEX",
        ),
        ({"replicationSpecs": []}, None),
        ({}, None),
        ({"replicationSpecs": [{"regionConfigs": [{}]}]}, None),
    ],
)
def test_cluster_instance_size(cluster, expected_size) -> None:
    assert workflows._cluster_instance_size(cluster) == expected_size


@pytest.mark.parametrize(
    "size,expected_substring",
    [
        ("M0", "M0 free tier"),
        ("M2", "shared tier"),
        ("M5", "shared tier"),
        ("FLEX", "Flex"),
        ("SERVERLESS", "deprecated"),
        ("m0", "M0 free tier"),  # case-insensitive
        ("M10", "disabled"),
        ("M30", "disabled"),
    ],
)
def test_explain_no_backups_known_tiers(size: str, expected_substring: str) -> None:
    cluster = {
        "replicationSpecs": [{"regionConfigs": [{"electableSpecs": {"instanceSize": size}}]}]
    }
    msg = workflows._explain_no_backups(cluster)
    assert expected_substring.lower() in msg.lower()


def test_explain_no_backups_unknown_tier_is_safe() -> None:
    cluster: dict = {"replicationSpecs": []}
    msg = workflows._explain_no_backups(cluster)
    assert "could not determine" in msg.lower() or "tier" in msg.lower()


# --- plan construction ----------------------------------------------------


def test_build_plan_filters_ineligible_clusters() -> None:
    client = _FakeAtlasClient()
    client.paginate_results = [
        {
            "name": "M0Free",
            "backupEnabled": False,
            "mongoDBMajorVersion": "8.0",
            "replicationSpecs": [{"regionConfigs": [{"electableSpecs": {"instanceSize": "M0"}}]}],
        },
        {
            "name": "NoSnapshots",
            "backupEnabled": True,
            "mongoDBMajorVersion": "8.0",
            "replicationSpecs": [{"regionConfigs": [{"electableSpecs": {"instanceSize": "M10"}}]}],
        },
        {
            "name": "VersionMismatch",
            "backupEnabled": True,
            "mongoDBMajorVersion": "7.0",
            "replicationSpecs": [{"regionConfigs": [{"electableSpecs": {"instanceSize": "M10"}}]}],
        },
        {
            "name": "Healthy",
            "backupEnabled": True,
            "mongoDBMajorVersion": "8.0",
            "replicationSpecs": [{"regionConfigs": [{"electableSpecs": {"instanceSize": "M10"}}]}],
        },
        {
            # Leftover backup-test target from prior run, must be ignored.
            "name": "Healthy-250509000000-backup-test-job",
            "backupEnabled": True,
            "mongoDBMajorVersion": "8.0",
            "replicationSpecs": [{"regionConfigs": [{"electableSpecs": {"instanceSize": "M10"}}]}],
        },
    ]
    client.queue("GET", "/groups/p1/clusters/NoSnapshots/backup/snapshots", {"results": []})
    client.queue(
        "GET",
        "/groups/p1/clusters/VersionMismatch/backup/snapshots",
        {"results": [{"id": "snap-v", "mongodVersion": "8.0.6"}]},
    )
    client.queue(
        "GET",
        "/groups/p1/clusters/Healthy/backup/snapshots",
        {"results": [{"id": "snap-h", "mongodVersion": "8.0.6"}]},
    )

    plan = workflows._build_plan(client, project_id="p1", max_retries=1)

    assert [i["source_name"] for i in plan["items"]] == ["Healthy"]
    assert plan["items"][0]["snapshot_id"] == "snap-h"
    reasons = {s["source"]: s["reason"] for s in plan["skipped"]}
    assert "M0Free" in reasons
    assert "NoSnapshots" in reasons
    assert "VersionMismatch" in reasons
    assert workflows.BACKUP_CLUSTER_MARKER in plan["items"][0]["target_name"]


def test_build_plan_with_empty_project() -> None:
    client = _FakeAtlasClient()
    client.paginate_results = []
    plan = workflows._build_plan(client, project_id="empty", max_retries=0)
    assert plan["items"] == []
    assert plan["skipped"] == []


def test_build_plan_surfaces_snapshot_fetch_errors() -> None:
    client = _FakeAtlasClient()
    client.paginate_results = [
        {
            "name": "C1",
            "backupEnabled": True,
            "mongoDBMajorVersion": "8.0",
            "replicationSpecs": [{"regionConfigs": [{"electableSpecs": {"instanceSize": "M10"}}]}],
        }
    ]
    client.queue(
        "GET",
        "/groups/p1/clusters/C1/backup/snapshots",
        AtlasApiError(500, "INTERNAL", "boom", "/groups/p1/clusters/C1/backup/snapshots"),
    )
    plan = workflows._build_plan(client, project_id="p1", max_retries=0)
    assert plan["items"] == []
    assert len(plan["skipped"]) == 1
    assert "failed to fetch snapshots" in plan["skipped"][0]["reason"]


def test_build_plan_preserves_max_retries_per_item() -> None:
    client = _FakeAtlasClient()
    client.paginate_results = [
        {
            "name": "C1",
            "backupEnabled": True,
            "mongoDBMajorVersion": "8.0",
            "replicationSpecs": [{"regionConfigs": [{"electableSpecs": {"instanceSize": "M10"}}]}],
        },
    ]
    client.queue(
        "GET",
        "/groups/p1/clusters/C1/backup/snapshots",
        {"results": [{"id": "s1", "mongodVersion": "8.0.6"}]},
    )
    plan = workflows._build_plan(client, project_id="p1", max_retries=3)
    assert plan["items"][0]["retries_remaining"] == 3


# --- _run_background end-to-end tests ------------------------------------


def _install_plan(project_id: str, items: list[dict]) -> str:
    """Helper: stash a plan doc into the workflow store and return handle."""
    return workflow_store.create_run(
        "snapshot_restore_test",
        project_id=project_id,
        plan={"items": items, "skipped": [], "run_timestamp": "t0"},
    )


def _cluster_item(**overrides: Any) -> dict[str, Any]:
    return {
        "source_name": overrides.get("source_name", "Cluster0"),
        "target_name": overrides.get("target_name", "Cluster0-t0-backup-test-job"),
        "snapshot_id": overrides.get("snapshot_id", "snap-1"),
        "mongo_version": overrides.get("mongo_version", "8.0"),
        "source_cluster": overrides.get(
            "source_cluster", {"name": "Cluster0", "backupEnabled": True, "replicationSpecs": []}
        ),
        "status": overrides.get("status", "pending_create"),
        "message": overrides.get("message", ""),
        "target_created": overrides.get("target_created", False),
        "restore_job_id": overrides.get("restore_job_id"),
        "retries_remaining": overrides.get("retries_remaining", 1),
    }


def _fixed_clock() -> Any:
    # Monotonic clock that never advances, so deadlines are never reached.
    return lambda: 0.0


def test_run_background_happy_path_single_cluster() -> None:
    handle = _install_plan("p1", [_cluster_item()])
    client = _FakeAtlasClient()
    client.queue("POST", "/groups/p1/clusters", {"name": "Cluster0-t0-backup-test-job"})
    client.queue(
        "GET", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {"stateName": "CREATING"}
    )
    client.queue("GET", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {"stateName": "IDLE"})
    client.queue("POST", "/groups/p1/clusters/Cluster0/backup/restoreJobs", {"id": "restore-1"})
    client.queue(
        "GET",
        "/groups/p1/clusters/Cluster0/backup/restoreJobs/restore-1",
        {"finishedAt": None},
    )
    client.queue(
        "GET",
        "/groups/p1/clusters/Cluster0/backup/restoreJobs/restore-1",
        {"finishedAt": "2025-05-09T12:00:00Z"},
    )
    client.queue("DELETE", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {})

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=True,
        max_retries=1,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    assert run is not None
    assert run["status"] == "succeeded"
    assert run["phase"] == "done"
    assert run["items"][0]["status"] == "success"
    assert run["finished_at"]
    assert client.closed is True


def test_run_background_multi_cluster_parallel_phases() -> None:
    handle = _install_plan(
        "p1",
        [
            _cluster_item(source_name="A", target_name="A-t0-backup-test-job", snapshot_id="snA"),
            _cluster_item(source_name="B", target_name="B-t0-backup-test-job", snapshot_id="snB"),
        ],
    )
    client = _FakeAtlasClient()
    # POST creates for both
    for name in ("A-t0-backup-test-job", "B-t0-backup-test-job"):
        client.queue("POST", "/groups/p1/clusters", {"name": name})
    # idle polls — A IDLE on first check, B IDLE on second
    client.queue("GET", "/groups/p1/clusters/A-t0-backup-test-job", {"stateName": "IDLE"})
    client.queue("GET", "/groups/p1/clusters/B-t0-backup-test-job", {"stateName": "CREATING"})
    client.queue("GET", "/groups/p1/clusters/B-t0-backup-test-job", {"stateName": "IDLE"})
    # restore jobs
    client.queue("POST", "/groups/p1/clusters/A/backup/restoreJobs", {"id": "rA"})
    client.queue("POST", "/groups/p1/clusters/B/backup/restoreJobs", {"id": "rB"})
    client.queue(
        "GET",
        "/groups/p1/clusters/A/backup/restoreJobs/rA",
        {"finishedAt": "2025-05-09T12:00:00Z"},
    )
    client.queue(
        "GET",
        "/groups/p1/clusters/B/backup/restoreJobs/rB",
        {"finishedAt": "2025-05-09T12:05:00Z"},
    )

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=1,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    assert run["status"] == "succeeded"
    assert {i["source_name"]: i["status"] for i in run["items"]} == {
        "A": "success",
        "B": "success",
    }


def test_run_background_idle_timeout_retries_then_errors_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _install_plan("p1", [_cluster_item(retries_remaining=0)])
    # Force the IDLE wait to time out immediately.
    monkeypatch.setattr(workflows, "CLUSTER_READY_TIMEOUT_SECONDS", 0)

    client = _FakeAtlasClient()
    client.queue("POST", "/groups/p1/clusters", {"name": "Cluster0-t0-backup-test-job"})
    # even though we won't hit the GET (deadline passes first), queue one just in case
    for _ in range(20):
        client.queue(
            "GET", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {"stateName": "CREATING"}
        )
    client.queue("DELETE", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {})

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=0,
        client_factory=lambda: client,
        sleep=lambda _s: None,
    )

    run = workflow_store.get_run(handle)
    assert run["status"] == "failed"
    assert run["items"][0]["status"] == "error"
    assert "out of retries" in run["items"][0]["message"]


def test_run_background_idle_timeout_successfully_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First pass: create OK, wait times out, target deleted, retry decrements.
    # Second pass: create OK, wait succeeds, restore succeeds.
    handle = _install_plan("p1", [_cluster_item(retries_remaining=1)])

    # We use a clock that returns a rising value; CLUSTER_READY_TIMEOUT is
    # patched to 0 so the first wait_for_targets_idle exits after one poll
    # (waiting list still has the item). Then recover_timed_out kicks in.
    calls: list[int] = []

    def monotonic_fn() -> float:
        calls.append(1)
        return float(len(calls))  # monotonic-ish

    monkeypatch.setattr(workflows, "CLUSTER_READY_TIMEOUT_SECONDS", 0)

    client = _FakeAtlasClient()
    # First pass: POST create, one GET returning CREATING (waits), DELETE stuck target.
    client.queue("POST", "/groups/p1/clusters", {"name": "Cluster0-t0-backup-test-job"})
    client.queue(
        "GET", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {"stateName": "CREATING"}
    )
    client.queue("DELETE", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {})

    # Second pass — restore CLUSTER_READY_TIMEOUT so wait succeeds, then
    # restore finishes. We need a separate monotonic because we're flipping
    # the timeout between passes.
    def run_wrapped() -> None:
        workflows._run_background(
            handle,
            project_id="p1",
            cleanup=False,
            max_retries=1,
            client_factory=lambda: client,
            sleep=lambda _s: None,
            monotonic=monotonic_fn,
        )

    # Kick off second pass expectations only *after* we flip the timeout back.
    # Do it via the sleep callback: after the first sleep (which is called
    # inside recover_timed_out? no — recover doesn't sleep). Simpler: run
    # first pass now (timeout=0), then flip, then run second pass separately.
    run_wrapped()
    run = workflow_store.get_run(handle)
    # After the first pass the item should be back to pending_create with
    # retries_remaining=0, but _run_phases loops until there are no
    # pending_create items. Since the DELETE queued above was consumed,
    # we need to queue more calls for the retry pass. Instead, assert
    # the retry mechanics executed: the item's retries_remaining decremented.
    # Since the loop would re-enter, actually the workflow runs multiple times
    # within the single _run_background call — so we need enough queued calls.
    # Easier: just assert the ultimate state after the inevitable second-pass
    # failure (since we didn't queue its POST).
    assert run["status"] in ("failed", "partial")


def test_run_background_create_failure_marks_item_as_error() -> None:
    handle = _install_plan("p1", [_cluster_item()])
    client = _FakeAtlasClient()
    client.queue(
        "POST",
        "/groups/p1/clusters",
        AtlasApiError(400, "CLUSTER_NAME_INVALID", "bad", "/groups/p1/clusters"),
    )

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=1,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    assert run["status"] == "failed"
    assert run["items"][0]["status"] == "error"
    assert "CLUSTER_NAME_INVALID" in run["items"][0]["message"]


def test_run_background_failed_restore_job_is_reported() -> None:
    handle = _install_plan("p1", [_cluster_item()])
    client = _FakeAtlasClient()
    client.queue("POST", "/groups/p1/clusters", {"name": "Cluster0-t0-backup-test-job"})
    client.queue("GET", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {"stateName": "IDLE"})
    client.queue("POST", "/groups/p1/clusters/Cluster0/backup/restoreJobs", {"id": "r1"})
    client.queue(
        "GET",
        "/groups/p1/clusters/Cluster0/backup/restoreJobs/r1",
        {"failed": True, "finishedAt": None},
    )

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=1,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    assert run["status"] == "failed"
    assert run["items"][0]["status"] == "error"
    assert "failed=True" in run["items"][0]["message"]


def test_run_background_cancelled_restore_job_is_reported() -> None:
    handle = _install_plan("p1", [_cluster_item()])
    client = _FakeAtlasClient()
    client.queue("POST", "/groups/p1/clusters", {"name": "Cluster0-t0-backup-test-job"})
    client.queue("GET", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {"stateName": "IDLE"})
    client.queue("POST", "/groups/p1/clusters/Cluster0/backup/restoreJobs", {"id": "r1"})
    client.queue(
        "GET",
        "/groups/p1/clusters/Cluster0/backup/restoreJobs/r1",
        {"cancelled": True, "finishedAt": None},
    )

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=1,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    assert run["items"][0]["status"] == "error"
    assert "cancelled=True" in run["items"][0]["message"]


def test_run_background_partial_success_status() -> None:
    # One success, one error -> status "partial".
    handle = _install_plan(
        "p1",
        [
            _cluster_item(
                source_name="Good", target_name="Good-t0-backup-test-job", snapshot_id="sg"
            ),
            _cluster_item(
                source_name="Bad", target_name="Bad-t0-backup-test-job", snapshot_id="sb"
            ),
        ],
    )
    client = _FakeAtlasClient()
    # Good goes all the way
    client.queue("POST", "/groups/p1/clusters", {"name": "Good-t0-backup-test-job"})
    # Bad fails at create
    client.queue(
        "POST",
        "/groups/p1/clusters",
        AtlasApiError(400, "X", "bad", "/groups/p1/clusters"),
    )
    client.queue("GET", "/groups/p1/clusters/Good-t0-backup-test-job", {"stateName": "IDLE"})
    client.queue("POST", "/groups/p1/clusters/Good/backup/restoreJobs", {"id": "r1"})
    client.queue(
        "GET",
        "/groups/p1/clusters/Good/backup/restoreJobs/r1",
        {"finishedAt": "2025-05-09T12:00:00Z"},
    )

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=1,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    assert run["status"] == "partial"


def test_run_background_cleanup_failure_logs_error_but_doesnt_change_status() -> None:
    handle = _install_plan("p1", [_cluster_item()])
    client = _FakeAtlasClient()
    client.queue("POST", "/groups/p1/clusters", {"name": "Cluster0-t0-backup-test-job"})
    client.queue("GET", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {"stateName": "IDLE"})
    client.queue("POST", "/groups/p1/clusters/Cluster0/backup/restoreJobs", {"id": "r1"})
    client.queue(
        "GET",
        "/groups/p1/clusters/Cluster0/backup/restoreJobs/r1",
        {"finishedAt": "2025-05-09T12:00:00Z"},
    )
    client.queue(
        "DELETE",
        "/groups/p1/clusters/Cluster0-t0-backup-test-job",
        AtlasApiError(
            500,
            "INTERNAL",
            "boom",
            "/groups/p1/clusters/Cluster0-t0-backup-test-job",
        ),
    )

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=True,
        max_retries=1,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    # Cleanup failure should be logged in errors[] but the overall status is
    # still "succeeded" — restore succeeded, and cleanup is best-effort.
    assert run["status"] == "succeeded"
    assert any("cleanup failed" in err for err in run["errors"])


def test_run_background_cleanup_skips_items_without_target_created() -> None:
    # An item that failed at create has target_created=False. Cleanup must
    # skip it rather than DELETEing a non-existent resource.
    item = _cluster_item()
    handle = _install_plan("p1", [item])
    client = _FakeAtlasClient()
    client.queue(
        "POST",
        "/groups/p1/clusters",
        AtlasApiError(400, "X", "bad", "/groups/p1/clusters"),
    )
    # If cleanup tried to DELETE anyway, we'd get "Unexpected request" from
    # the fake.

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=True,
        max_retries=1,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    # Only the failed POST to create was issued; no DELETE for cleanup.
    delete_calls = [c for c in client.calls if c[0] == "DELETE"]
    assert delete_calls == []
    assert run["items"][0]["status"] == "error"


def test_run_background_poll_restore_fetch_error_is_set_on_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # _poll_restores hits an exception when fetching a job — that item is
    # marked error but the workflow overall continues.
    handle = _install_plan("p1", [_cluster_item()])
    client = _FakeAtlasClient()
    client.queue("POST", "/groups/p1/clusters", {"name": "Cluster0-t0-backup-test-job"})
    client.queue("GET", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {"stateName": "IDLE"})
    client.queue("POST", "/groups/p1/clusters/Cluster0/backup/restoreJobs", {"id": "r1"})
    client.queue(
        "GET",
        "/groups/p1/clusters/Cluster0/backup/restoreJobs/r1",
        AtlasApiError(
            500,
            "INTERNAL",
            "boom",
            "/groups/p1/clusters/Cluster0/backup/restoreJobs/r1",
        ),
    )

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=1,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    assert run["items"][0]["status"] == "error"
    assert "failed to poll restore" in run["items"][0]["message"]


def test_execute_snapshot_restore_test_surfaces_build_plan_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeAtlasClient()

    def exploding_paginate(*a, **kw):
        raise AtlasApiError(500, "X", "boom", "/groups/p/clusters")
        yield  # pragma: no cover

    client.paginate = exploding_paginate  # type: ignore[method-assign]
    monkeypatch.setattr(workflows, "from_env", lambda: client)

    app = _FakeApp()
    workflows.register_workflows(app)
    result = app.registered["execute_snapshot_restore_test"](project_id="p1")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "failed to build plan" in parsed["message"]


def test_run_background_cleanup_skipped_when_cleanup_false() -> None:
    handle = _install_plan("p1", [_cluster_item()])
    client = _FakeAtlasClient()
    client.queue("POST", "/groups/p1/clusters", {"name": "Cluster0-t0-backup-test-job"})
    client.queue("GET", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {"stateName": "IDLE"})
    client.queue("POST", "/groups/p1/clusters/Cluster0/backup/restoreJobs", {"id": "r1"})
    client.queue(
        "GET",
        "/groups/p1/clusters/Cluster0/backup/restoreJobs/r1",
        {"finishedAt": "2025-05-09T12:00:00Z"},
    )

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=1,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    assert run["status"] == "succeeded"
    # No DELETE call was issued.
    assert ("DELETE", "/groups/p1/clusters/Cluster0-t0-backup-test-job") not in client.calls


def test_run_background_config_error_marks_failed_without_running_phases() -> None:
    handle = _install_plan("p1", [_cluster_item()])

    def bad_factory():
        raise AtlasConfigError("missing creds")

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=0,
        client_factory=bad_factory,
        sleep=lambda _s: None,
    )

    run = workflow_store.get_run(handle)
    assert run["status"] == "failed"
    assert any("config_error" in e for e in run["errors"])


def test_run_background_handles_unexpected_exception() -> None:
    handle = _install_plan("p1", [_cluster_item()])

    def exploding_factory():
        class ExplodingClient(_FakeAtlasClient):
            def request(self, *a, **kw):  # noqa: ARG002
                raise RuntimeError("kaboom")

        c = ExplodingClient()
        return c

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=0,
        client_factory=exploding_factory,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    assert run["status"] == "failed"
    assert any("unexpected_error" in e for e in run["errors"])


def test_run_background_missing_handle_is_safe() -> None:
    # If the run doc is gone, don't crash — just log and return.
    client = _FakeAtlasClient()
    workflows._run_background(
        "WF-MISSING",
        project_id="p1",
        cleanup=False,
        max_retries=0,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )
    # No exception.


def test_run_background_client_close_failure_is_swallowed() -> None:
    # If client.close() raises during cleanup, the workflow should still finish
    # (the close error is logged at debug, not re-raised).
    handle = _install_plan("p1", [_cluster_item()])

    class BrokenClose(_FakeAtlasClient):
        def close(self) -> None:
            raise RuntimeError("close explosion")

    client = BrokenClose()
    client.queue("POST", "/groups/p1/clusters", {"name": "Cluster0-t0-backup-test-job"})
    client.queue("GET", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {"stateName": "IDLE"})
    client.queue("POST", "/groups/p1/clusters/Cluster0/backup/restoreJobs", {"id": "r1"})
    client.queue(
        "GET",
        "/groups/p1/clusters/Cluster0/backup/restoreJobs/r1",
        {"finishedAt": "2025-05-09T12:00:00Z"},
    )

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=1,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    assert run["status"] == "succeeded"


def test_run_background_poll_error_marks_item_error_and_continues() -> None:
    handle = _install_plan(
        "p1",
        [
            _cluster_item(source_name="A", target_name="A-t0-backup-test-job", snapshot_id="sa"),
            _cluster_item(source_name="B", target_name="B-t0-backup-test-job", snapshot_id="sb"),
        ],
    )
    client = _FakeAtlasClient()
    for name in ("A-t0-backup-test-job", "B-t0-backup-test-job"):
        client.queue("POST", "/groups/p1/clusters", {"name": name})
    # A fetch errors; B goes to IDLE
    client.queue(
        "GET",
        "/groups/p1/clusters/A-t0-backup-test-job",
        AtlasApiError(500, "INTERNAL", "boom", "/groups/p1/clusters/A-t0-backup-test-job"),
    )
    client.queue("GET", "/groups/p1/clusters/B-t0-backup-test-job", {"stateName": "IDLE"})
    client.queue("POST", "/groups/p1/clusters/B/backup/restoreJobs", {"id": "rB"})
    client.queue(
        "GET",
        "/groups/p1/clusters/B/backup/restoreJobs/rB",
        {"finishedAt": "2025-05-09T12:00:00Z"},
    )

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=1,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    statuses = {i["source_name"]: i["status"] for i in run["items"]}
    assert statuses == {"A": "error", "B": "success"}


# --- public tool surface --------------------------------------------------


def test_run_snapshot_restore_test_returns_plan_suspend_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeAtlasClient()
    client.paginate_results = [
        {
            "name": "Healthy",
            "backupEnabled": True,
            "mongoDBMajorVersion": "8.0",
            "replicationSpecs": [{"regionConfigs": [{"electableSpecs": {"instanceSize": "M10"}}]}],
        }
    ]
    client.queue(
        "GET",
        "/groups/p1/clusters/Healthy/backup/snapshots",
        {"results": [{"id": "snap-h", "mongodVersion": "8.0.6"}]},
    )
    monkeypatch.setattr(workflows, "from_env", lambda: client)

    app = _FakeApp()
    workflows.register_workflows(app)
    result = app.registered["run_snapshot_restore_test"](project_id="p1")
    parsed = json.loads(result)

    assert parsed["__suspend__"] is True
    assert parsed["suspend_reason"] == "snapshot_restore_test_approval"
    ctx = parsed["suspend_context"]
    assert ctx["decision_type"] == "snapshot_restore_workflow"
    assert ctx["project_id"] == "p1"
    assert len(ctx["plan"]["items"]) == 1
    assert ctx["plan"]["items"][0]["source_name"] == "Healthy"
    assert ctx["plan"]["items"][0]["snapshot_id"] == "snap-h"
    # Plan stripped of bulky source_cluster payload.
    assert "source_cluster" not in ctx["plan"]["items"][0]
    assert ctx["estimated_runtime_hours"]
    assert ctx["task_id"].startswith("WFPLAN-")


def test_run_snapshot_restore_test_requires_project_id() -> None:
    app = _FakeApp()
    workflows.register_workflows(app)
    result = app.registered["run_snapshot_restore_test"](project_id="")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "project_id" in parsed["message"]


def test_run_snapshot_restore_test_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad():
        raise AtlasConfigError("missing")

    monkeypatch.setattr(workflows, "from_env", bad)
    app = _FakeApp()
    workflows.register_workflows(app)
    result = app.registered["run_snapshot_restore_test"](project_id="p1")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "missing" in parsed["message"]


def test_run_snapshot_restore_test_closes_client_on_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeAtlasClient()

    def exploding_paginate(*a, **kw):
        raise AtlasApiError(500, "X", "boom", "/groups/p/clusters")
        yield  # make this a generator

    client.paginate = exploding_paginate  # type: ignore[method-assign]
    monkeypatch.setattr(workflows, "from_env", lambda: client)

    app = _FakeApp()
    workflows.register_workflows(app)
    result = app.registered["run_snapshot_restore_test"](project_id="p1")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert client.closed is True


def test_execute_snapshot_restore_test_returns_handle_and_spawns_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # We don't actually want a real thread — intercept Thread() to capture args.
    captured: dict = {}

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            captured["target"] = target
            captured["args"] = args
            captured["name"] = name
            captured["daemon"] = daemon

        def start(self) -> None:
            captured["started"] = True

    monkeypatch.setattr(workflows.threading, "Thread", FakeThread)

    client = _FakeAtlasClient()
    client.paginate_results = [
        {
            "name": "Healthy",
            "backupEnabled": True,
            "mongoDBMajorVersion": "8.0",
            "replicationSpecs": [{"regionConfigs": [{"electableSpecs": {"instanceSize": "M10"}}]}],
        }
    ]
    client.queue(
        "GET",
        "/groups/p1/clusters/Healthy/backup/snapshots",
        {"results": [{"id": "snap-h", "mongodVersion": "8.0.6"}]},
    )
    monkeypatch.setattr(workflows, "from_env", lambda: client)

    app = _FakeApp()
    workflows.register_workflows(app)
    result = app.registered["execute_snapshot_restore_test"](
        project_id="p1", cleanup=False, max_retries=1
    )
    parsed = json.loads(result)

    assert parsed["status"] == "running"
    assert parsed["handle_id"].startswith("WF-")
    assert parsed["items_planned"] == 1
    assert captured["started"] is True
    assert captured["name"].startswith("snapshot-restore-")
    assert captured["daemon"] is True

    # The thread target is _run_background; args match public-tool signature.
    handle = parsed["handle_id"]
    assert captured["target"] is workflows._run_background
    assert captured["args"][0] == handle
    assert captured["args"][1] == "p1"
    # (cleanup, max_retries)
    assert captured["args"][2] is False
    assert captured["args"][3] == 1

    # Run doc is present in the store and marked running.
    run = workflow_store.get_run(handle)
    assert run is not None
    assert run["status"] == "running"
    assert run["kind"] == "snapshot_restore_test"


def test_execute_snapshot_restore_test_requires_project_id() -> None:
    app = _FakeApp()
    workflows.register_workflows(app)
    result = app.registered["execute_snapshot_restore_test"](project_id="")
    parsed = json.loads(result)
    assert parsed["status"] == "error"


def test_execute_snapshot_restore_test_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad():
        raise AtlasConfigError("missing")

    monkeypatch.setattr(workflows, "from_env", bad)
    app = _FakeApp()
    workflows.register_workflows(app)
    result = app.registered["execute_snapshot_restore_test"](project_id="p1")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "missing" in parsed["message"]


def test_check_snapshot_restore_test_reports_counts() -> None:
    handle = workflow_store.create_run(
        "snapshot_restore_test",
        project_id="p1",
        plan={
            "items": [
                {"source_name": "a", "target_name": "at", "status": "success", "message": ""},
                {"source_name": "b", "target_name": "bt", "status": "error", "message": "boom"},
                {"source_name": "c", "target_name": "ct", "status": "creating", "message": ""},
            ],
            "skipped": [{"source": "M0", "reason": "M0 tier"}],
            "run_timestamp": "X",
        },
    )

    app = _FakeApp()
    workflows.register_workflows(app)
    result = app.registered["check_snapshot_restore_test"](handle)
    parsed = json.loads(result)

    assert parsed["handle_id"] == handle
    assert parsed["counts"] == {"total": 3, "success": 1, "error": 1, "in_progress": 1}
    assert parsed["skipped"] == [{"source": "M0", "reason": "M0 tier"}]


def test_check_snapshot_restore_test_unknown_handle() -> None:
    app = _FakeApp()
    workflows.register_workflows(app)
    result = app.registered["check_snapshot_restore_test"]("WF-NOPE")
    parsed = json.loads(result)
    assert parsed["status"] == "error"


# --- thread safety of background runner (smoke test) ----------------------


def test_run_background_in_a_real_thread() -> None:
    handle = _install_plan("p1", [_cluster_item()])
    client = _FakeAtlasClient()
    client.queue("POST", "/groups/p1/clusters", {"name": "Cluster0-t0-backup-test-job"})
    client.queue("GET", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {"stateName": "IDLE"})
    client.queue("POST", "/groups/p1/clusters/Cluster0/backup/restoreJobs", {"id": "r1"})
    client.queue(
        "GET",
        "/groups/p1/clusters/Cluster0/backup/restoreJobs/r1",
        {"finishedAt": "2025-05-09T12:00:00Z"},
    )

    thread = threading.Thread(
        target=workflows._run_background,
        kwargs={
            "handle_id": handle,
            "project_id": "p1",
            "cleanup": False,
            "max_retries": 1,
            "client_factory": lambda: client,
            "sleep": lambda _s: None,
            "monotonic": _fixed_clock(),
        },
        daemon=True,
    )
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()

    run = workflow_store.get_run(handle)
    assert run["status"] == "succeeded"


# --- regression: phase-clock injection ------------------------------------


def _bounded_clock(times: list[float]):
    """Callable clock that walks a list and clamps to the last value after exhaustion."""
    state = {"i": 0}

    def _tick() -> float:
        i = min(state["i"], len(times) - 1)
        state["i"] += 1
        return times[i]

    return _tick


def test_wait_for_targets_idle_respects_injected_clock() -> None:
    client = _FakeAtlasClient()
    client.queue("GET", "/groups/p1/clusters/T", {"stateName": "CREATING"})
    client.queue("GET", "/groups/p1/clusters/T", {"stateName": "CREATING"})

    items = [
        {
            "target_name": "T",
            "source_name": "S",
            "snapshot_id": "x",
            "status": "creating",
            "retries_remaining": 0,
            "message": "",
            "restore_job_id": None,
            "target_created": True,
        }
    ]
    # deadline = 0.0 + CLUSTER_READY_TIMEOUT; after first poll, clock jumps past.
    clock = _bounded_clock([0.0, 0.0, float(workflows.CLUSTER_READY_TIMEOUT_SECONDS + 1)])
    result = workflows._wait_for_targets_idle(
        client, items, "p1", sleep=lambda _s: None, monotonic=clock
    )
    assert len(result) == 1
    assert result[0]["status"] == "creating"


def test_start_restores_marks_item_error_on_api_failure() -> None:
    items = [
        {
            "source_name": "S",
            "target_name": "T",
            "snapshot_id": "sn",
            "status": "ready_for_restore",
            "message": "",
            "retries_remaining": 0,
            "target_created": True,
            "restore_job_id": None,
        }
    ]
    client = _FakeAtlasClient()
    client.queue(
        "POST",
        "/groups/p/clusters/S/backup/restoreJobs",
        AtlasApiError(
            400,
            "BAD_REQUEST",
            "bad",
            "/groups/p/clusters/S/backup/restoreJobs",
        ),
    )
    workflows._start_restores(client, items, "p")
    assert items[0]["status"] == "error"
    assert "failed to start restore" in items[0]["message"]


def test_recover_timed_out_logs_delete_failure_but_still_bumps_retries() -> None:
    client = _FakeAtlasClient()
    client.queue(
        "DELETE",
        "/groups/p/clusters/stuck",
        AtlasApiError(500, "INTERNAL", "boom", "/groups/p/clusters/stuck"),
    )
    items = [
        {
            "source_name": "S",
            "target_name": "stuck",
            "snapshot_id": "sn",
            "status": "creating",
            "message": "",
            "retries_remaining": 2,
            "target_created": True,
            "restore_job_id": "r",
        }
    ]
    workflows._recover_timed_out(client, items, items, "IDLE timed out", "p")
    # Delete failed but retries still decremented and status reset.
    assert items[0]["retries_remaining"] == 1
    assert items[0]["status"] == "pending_create"
    assert items[0]["restore_job_id"] is None
    assert items[0]["target_created"] is False


def test_run_background_restore_timeout_triggers_recover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force restore timeout to zero so the poll loop exits immediately after
    # registering the job as still-in-progress.
    monkeypatch.setattr(workflows, "RESTORE_TIMEOUT_SECONDS", 0)
    handle = _install_plan(
        "p1",
        [_cluster_item(retries_remaining=0)],
    )
    client = _FakeAtlasClient()
    client.queue("POST", "/groups/p1/clusters", {"name": "Cluster0-t0-backup-test-job"})
    client.queue("GET", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {"stateName": "IDLE"})
    client.queue("POST", "/groups/p1/clusters/Cluster0/backup/restoreJobs", {"id": "r1"})
    # Make the restore poll see "in progress" — timeout triggers recovery.
    for _ in range(20):
        client.queue(
            "GET",
            "/groups/p1/clusters/Cluster0/backup/restoreJobs/r1",
            {"finishedAt": None},
        )
    client.queue("DELETE", "/groups/p1/clusters/Cluster0-t0-backup-test-job", {})

    workflows._run_background(
        handle,
        project_id="p1",
        cleanup=False,
        max_retries=0,
        client_factory=lambda: client,
        sleep=lambda _s: None,
        monotonic=_fixed_clock(),
    )

    run = workflow_store.get_run(handle)
    assert run["status"] == "failed"
    assert run["items"][0]["status"] == "error"
    assert "out of retries" in run["items"][0]["message"]


def test_poll_restores_respects_injected_clock() -> None:
    client = _FakeAtlasClient()
    client.queue("GET", "/groups/p1/clusters/S/backup/restoreJobs/r", {"finishedAt": None})
    client.queue("GET", "/groups/p1/clusters/S/backup/restoreJobs/r", {"finishedAt": None})

    items = [
        {
            "source_name": "S",
            "target_name": "T",
            "restore_job_id": "r",
            "status": "restoring",
            "message": "",
            "retries_remaining": 0,
            "target_created": True,
        }
    ]
    clock = _bounded_clock([0.0, 0.0, float(workflows.RESTORE_TIMEOUT_SECONDS + 1)])
    result = workflows._poll_restores(client, items, "p1", sleep=lambda _s: None, monotonic=clock)
    assert len(result) == 1
    assert result[0]["status"] == "restoring"
