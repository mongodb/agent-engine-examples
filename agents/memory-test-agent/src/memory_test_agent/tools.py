"""Memory surface-area and OE access-policy test tools.

Two test groups:
  1. SDK happy-path — exercises app.memory.* end-to-end through the full stack
  2. OE policy — makes direct HTTP calls with X-Agentic-Execution-Id to verify
     that the OE memory proxy enforces the three access rules and scope checks

Run with:
  curl -s -X POST http://localhost:<oe-port>/invoke \
    -H "Content-Type: application/json" \
    -d '{"message":"run all scenarios","user_id":"test-user","session_id":"s1"}'
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OTHER_USER = "other-user-00000000"

# build_context retrieves via Atlas $vectorSearch, whose index is eventually
# consistent: a just-written memory (embedded synchronously on write) is not
# immediately searchable — the index catches up asynchronously, typically a few
# seconds but occasionally longer under load. Poll a freshly written memory into
# its author's own context for up to this many seconds before treating it as a
# failure, so normal index lag doesn't flake the isolation checks.
_INDEX_POLL_SECONDS = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts() -> str:
    return str(int(time.time()))


def _current_user() -> str:
    try:
        from runner_shared.context import get_current_user_id

        uid = get_current_user_id()
        if uid:
            return uid
    except Exception:
        pass
    return "memory-test-user"


def _execution_id() -> str:
    try:
        from runner_shared.context import get_current_execution_id

        eid = get_current_execution_id()
        if eid:
            return eid
    except Exception:
        pass
    return ""


def _oe_url() -> str:
    try:
        from runner_shared.context import get_current_oe_url

        url = get_current_oe_url()
        if url:
            return url.rstrip("/")
    except Exception:
        pass
    return os.environ.get("OE_URL", "http://oe:8000").rstrip("/")


def _org_id() -> str:
    return os.environ.get("ORG_ID", "000000000000000000000001")


def _project_id() -> str:
    return os.environ.get("PROJECT_ID", "000000000000000000000002")


def _http_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if eid := _execution_id():
        h["X-Agentic-Execution-Id"] = eid
    return h


# OE memory-proxy prefix. The OE namespaces all memory routes under
# /api/v1/memory/ and strips that segment before forwarding to the
# memory-server (see orchestration-engine handlers.go). Keep this aligned with
# the SDK's MemoryClient api_prefix so a future route change is a single edit
# here rather than per-call-site.
_MEMORY_API_PREFIX = "/api/v1/memory"


def _post(path: str, body: dict[str, Any]) -> tuple[int, Any]:
    try:
        resp = httpx.post(
            f"{_oe_url()}{_MEMORY_API_PREFIX}/{path}",
            json=body,
            headers=_http_headers(),
            timeout=10.0,
        )
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, resp.text
    except Exception as exc:
        return 0, str(exc)



# Result helpers
def _ok(label: str, result: Any) -> tuple[str, bool]:
    passed = bool(result)
    icon = "✅" if passed else "❌"
    detail = repr(result)[:80] if passed else repr(result)
    return f"{icon} {label}: {detail}", passed


def _expect_status(label: str, got: int, expected: int) -> tuple[str, bool]:
    passed = got == expected
    icon = "✅" if passed else "❌"
    return f"{icon} {label}: HTTP {got} (expected {expected})", passed


# ---------------------------------------------------------------------------
# SDK happy-path scenarios
# ---------------------------------------------------------------------------


def _sdk_semantic(memory: Any, ts: str) -> list[tuple[str, bool]]:
    results = []
    uid = _current_user()

    ok = memory.save_semantic(
        text="Deductible is the out-of-pocket amount before coverage starts.",
        label=f"sem-private-{ts}",
        user_id=uid,
        visibility="private",
    )
    results.append(_ok("sdk/semantic/save_private", ok))

    ok = memory.save_semantic(
        text="Org-wide tip: always read the policy exclusions.",
        label=f"sem-org-{ts}",
        user_id=uid,
        visibility="org",
    )
    results.append(_ok("sdk/semantic/save_org", ok))

    return results


def _sdk_episodic(memory: Any, ts: str) -> list[tuple[str, bool]]:
    results = []
    uid = _current_user()

    ep_id = memory.save_episode(
        title=f"Claim Review {ts}",
        content="Reviewed and approved claim #999.",
        user_id=uid,
        session_id=f"ep-session-{ts}",
        visibility="private",
    )
    results.append(_ok("sdk/episodic/save", ep_id))

    episodes = memory.list_episodes(user_id=uid, visibility="private")
    found = any(
        (e.get("title") if isinstance(e, dict) else getattr(e, "title", None))
        == f"Claim Review {ts}"
        for e in episodes
    )
    results.append(("✅ sdk/episodic/list" if found else "❌ sdk/episodic/list", found))

    return results


def _sdk_taxonomic(memory: Any, ts: str) -> list[tuple[str, bool]]:
    results = []
    uid = _current_user()
    domain = f"domain-{ts}"

    tax_id = memory.save_taxonomic(
        domain=domain,
        term="premium",
        definition="Periodic payment for insurance coverage.",
        user_id=uid,
        visibility="org",
    )
    results.append(_ok("sdk/taxonomic/save", tax_id))

    term = memory.get_taxonomic_term(domain=domain, term="premium", visibility="org")
    results.append(_ok("sdk/taxonomic/get_term", term))

    domains = memory.list_domains(visibility="org")
    results.append((
        "✅ sdk/taxonomic/list_domains" if domain in domains else "❌ sdk/taxonomic/list_domains",
        domain in domains,
    ))

    return results


def _sdk_procedural(memory: Any, ts: str) -> list[tuple[str, bool]]:
    results = []
    uid = _current_user()
    proc = f"proc-{ts}"

    saved = memory.save_procedure(
        procedure=proc,
        description="A test procedure.",
        content="Step 1: verify. Step 2: assert.",
        user_id=uid,
        visibility="private",
    )
    results.append(_ok("sdk/procedural/save", saved))

    fetched = memory.get_procedure(procedure_name=proc, user_id=uid, visibility="private")
    results.append(_ok("sdk/procedural/get", fetched))

    return results


def _sdk_context(memory: Any, ts: str) -> list[tuple[str, bool]]:
    results = []
    uid = _current_user()
    session = f"ctx-{ts}"

    ctx = memory.build_context(
        query="insurance deductible",
        user_id=uid,
        session_id=session,
        visibility="private",
    )
    ok_private = isinstance(ctx, str) and len(ctx) > 0
    results.append((
        "✅ sdk/context/build_private" if ok_private else "❌ sdk/context/build_private: empty or non-string",
        ok_private,
    ))

    ctx = memory.build_context(
        query="org knowledge",
        user_id=uid,
        session_id=session,
        visibility="org",
    )
    ok_org = isinstance(ctx, str) and len(ctx) > 0
    results.append((
        "✅ sdk/context/build_org" if ok_org else "❌ sdk/context/build_org: empty or non-string",
        ok_org,
    ))

    return results


def _sdk_context_isolation(memory: Any, ts: str) -> list[tuple[str, bool]]:
    """AP-184: build_context must not surface another user's private memories.

    user_a is the execution principal so the write is authorised. user_b is a
    synthetic ID that differs from the principal; the OE proxy should reject or
    return empty context for private cross-user reads (Rule 1), so the sentinel
    must not appear in user_b's view. We poll user_a's context first to confirm
    the write was indexed before checking user_b (guards against async indexing
    giving a vacuous pass).
    """
    results = []
    user_a = _current_user()
    user_b = f"isolation-user-b-{ts}"
    private_sentinel = f"PRIVATE_SENTINEL_{ts}"
    org_sentinel = f"ORG_SENTINEL_{ts}"
    session = f"isolation-session-{ts}"

    # Step 1: write a private semantic memory as user-A (execution principal)
    ok = memory.save_semantic(
        text=f"Secret data: {private_sentinel}",
        label=f"isolation-private-{ts}",
        user_id=user_a,
        visibility="private",
    )
    results.append(_ok("context/isolation_write_private_user_a", ok))

    # Step 2: write an org-scoped semantic memory as user-A
    ok_org = memory.save_semantic(
        text=f"Org knowledge: {org_sentinel}",
        label=f"isolation-org-{ts}",
        user_id=user_a,
        visibility="org",
    )
    results.append(_ok("context/isolation_write_org_user_a", ok_org))

    # Poll user-A's private context until the private sentinel is indexed
    # (Atlas vector-index lag — see _INDEX_POLL_SECONDS).
    ctx_a = ""
    for _ in range(_INDEX_POLL_SECONDS):
        ctx_a = memory.build_context(
            query=private_sentinel,
            user_id=user_a,
            session_id=session,
            visibility="private",
        )
        if isinstance(ctx_a, str) and private_sentinel in ctx_a:
            break
        time.sleep(1.0)

    a_sees_own_private = isinstance(ctx_a, str) and private_sentinel in ctx_a
    results.append((
        "✅ context/isolation_user_a_sees_own_private_memory" if a_sees_own_private
        else "❌ context/isolation_user_a_sees_own_private_memory: sentinel not found in own context",
        a_sees_own_private,
    ))

    # Poll user-A's org context until the org sentinel is indexed
    # (Atlas vector-index lag — see _INDEX_POLL_SECONDS).
    ctx_a_org = ""
    for _ in range(_INDEX_POLL_SECONDS):
        ctx_a_org = memory.build_context(
            query=org_sentinel,
            user_id=user_a,
            session_id=session,
            visibility="org",
        )
        if isinstance(ctx_a_org, str) and org_sentinel in ctx_a_org:
            break
        time.sleep(1.0)

    # Step 3: user-B builds context with visibility=private — must NOT see user-A's private memory
    ctx_b_private = memory.build_context(
        query=private_sentinel,
        user_id=user_b,
        session_id=session,
        visibility="private",
    )
    not_leaked = isinstance(ctx_b_private, str) and private_sentinel not in ctx_b_private
    results.append((
        "✅ context/isolation_user_b_cannot_see_user_a_private" if not_leaked
        else "❌ context/isolation_user_b_cannot_see_user_a_private: sentinel found in context",
        not_leaked,
    ))

    # Step 4: an org-scoped read MUST surface user-A's org memory.
    #
    # Org visibility is user-agnostic by design: org-scoped memories belong to
    # the org, not a user. The SDK's read contract is "omit user_id for an
    # org-scope read" — passing visibility without user_id leaves user_id out of
    # the server filter ({org_id, visibility: org}), matching every org member's
    # org memories. (Passing a specific user_id here would instead pin the
    # filter to that user's own docs, so a synthetic user with no writes would
    # see nothing — that is correct private-scoping, not an org read.) The
    # cross-user *private* isolation guarantee is already covered by Step 3.
    # Poll until the org sentinel is indexed (Atlas vector-index lag — see
    # _INDEX_POLL_SECONDS), mirroring the author-context polls above. This is a
    # single read with no natural retry, so without the poll it races the index.
    ctx_org = ""
    for _ in range(_INDEX_POLL_SECONDS):
        ctx_org = memory.build_context(
            query=org_sentinel,
            session_id=session,
            visibility="org",
        )
        if isinstance(ctx_org, str) and org_sentinel in ctx_org:
            break
        time.sleep(1.0)
    sees_org = isinstance(ctx_org, str) and org_sentinel in ctx_org
    results.append((
        "✅ context/isolation_org_scope_sees_org_memory" if sees_org
        else "❌ context/isolation_org_scope_sees_org_memory: org sentinel not found",
        sees_org,
    ))

    return results


# ---------------------------------------------------------------------------
# OE access-policy scenarios (direct HTTP)
# ---------------------------------------------------------------------------


def _policy_scenarios(ts: str) -> list[tuple[str, bool]]:
    """Verify OE memory proxy enforces the three access rules and scope checks."""
    results = []
    uid = _current_user()
    exec_id = _execution_id()
    logger.info(f"Policy tests: uid={uid} exec_id={exec_id} headers={_http_headers()}")
    org = _org_id()
    proj = _project_id()

    # --- Happy paths ---

    # Own write → 201
    s, _ = _post("semantic", {
        "org_id": org, "project_id": proj,
        "label": f"policy-write-{ts}", "text": "own write test",
        "user_id": uid, "visibility": "private", "source": "test",
    })
    results.append(_expect_status("policy/own_write (expect 201)", s, 201))

    # Own retrieval with visibility → 200
    s, _ = _post("retrieval/semantic", {
        "org_id": org, "project_id": proj,
        "query": "test", "user_id": uid, "visibility": "private",
    })
    results.append(_expect_status("policy/own_read_private (expect 200)", s, 200))

    # Cross-user with org visibility → 200 (allowed)
    s, _ = _post("retrieval/semantic", {
        "org_id": org, "project_id": proj,
        "query": "test", "user_id": OTHER_USER, "visibility": "org",
    })
    results.append(_expect_status("policy/cross_user_org (expect 200)", s, 200))

    # Cross-user with shared visibility → 200 (allowed)
    s, _ = _post("retrieval/semantic", {
        "org_id": org, "project_id": proj,
        "query": "test", "user_id": OTHER_USER, "visibility": "shared",
    })
    results.append(_expect_status("policy/cross_user_shared (expect 200)", s, 200))

    # --- Rule 0: no user_id and no visibility → 400 ---
    s, _ = _post("retrieval/semantic", {
        "org_id": org, "project_id": proj, "query": "test",
    })
    results.append(_expect_status("policy/Rule0_no_identity (expect 400)", s, 400))

    # --- Rule 1: visibility=private + user_id != principal → 403 ---
    s, _ = _post("retrieval/semantic", {
        "org_id": org, "project_id": proj,
        "query": "test", "user_id": OTHER_USER, "visibility": "private",
    })
    results.append(_expect_status("policy/Rule1_private_cross_user (expect 403)", s, 403))

    # Rule 1 on episodic retrieval
    s, _ = _post("retrieval/episodic", {
        "org_id": org, "project_id": proj,
        "query": "test", "user_id": OTHER_USER, "visibility": "private",
    })
    results.append(_expect_status("policy/Rule1_episodic_cross_user (expect 403)", s, 403))

    # --- Rule 2: cross-user with no visibility → 403 ---
    s, _ = _post("retrieval/semantic", {
        "org_id": org, "project_id": proj,
        "query": "test", "user_id": OTHER_USER,
    })
    results.append(_expect_status("policy/Rule2_cross_user_no_scope (expect 403)", s, 403))

    # Scope checks are skipped here: executions created via /invoke don't carry
    # org/project in the AER context, so the OE proxy has nothing to compare
    # against. The scoped case is covered by OE SDK integration tests.

    # --- Build context: cross-user private → 403 (visibility enforced) ---
    s, _ = _post("retrieval/context", {
        "org_id": org, "project_id": proj,
        "query": "test", "user_id": OTHER_USER,
        "session_id": f"policy-ctx-{ts}", "visibility": "private",
    })
    results.append(_expect_status("policy/context_cross_user_private (expect 403)", s, 403))

    # AP-184 Rule 1: visibility=private set, user_id absent (empty != principal) → 403
    # This mirrors the Go SDK test step 5: visibility is non-empty so Rule 0 doesn't
    # fire, but missing user_id is treated as empty string which != principal.
    s, _ = _post("retrieval/context", {
        "org_id": org, "project_id": proj,
        "query": "test",
        "session_id": f"policy-ctx-noid-{ts}", "visibility": "private",
    })
    results.append(_expect_status("policy/context_no_user_id_private (expect 403)", s, 403))

    # --- Build context: cross-user org → 200 ---
    s, _ = _post("retrieval/context", {
        "org_id": org, "project_id": proj,
        "query": "test", "user_id": OTHER_USER,
        "session_id": f"policy-ctx-{ts}", "visibility": "org",
    })
    results.append(_expect_status("policy/context_cross_user_org (expect 200)", s, 200))

    # --- STM write: own turn → 201 ---
    s, _ = _post("stm/turns", {
        "org_id": org, "project_id": proj,
        "session_id": f"stm-{ts}", "role": "user",
        "content": "test turn", "user_id": uid, "visibility": "private",
    })
    results.append(_expect_status("policy/stm_write_own (expect 201)", s, 201))

    return results


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(app: Any) -> dict:

    @app.tool(is_local=True)
    def run_all_scenarios() -> str:
        """Run SDK surface-area + OE access-policy tests and return a report."""
        memory = app.memory
        ts = _ts()

        sdk_results: list[tuple[str, bool]] = []
        sdk_results += _sdk_semantic(memory, ts)
        sdk_results += _sdk_episodic(memory, ts)
        sdk_results += _sdk_taxonomic(memory, ts)
        sdk_results += _sdk_procedural(memory, ts)
        sdk_results += _sdk_context(memory, ts)
        sdk_results += _sdk_context_isolation(memory, ts)

        policy_results = _policy_scenarios(ts)

        all_results = sdk_results + policy_results
        passed = sum(1 for _, ok in all_results if ok)
        total = len(all_results)

        lines = ["## Memory Test Results\n"]

        lines.append("\n### SDK Surface-Area (write/read/context)")
        for msg, _ in sdk_results:
            lines.append(msg)

        lines.append("\n### OE Access Policy Enforcement")
        for msg, _ in policy_results:
            lines.append(msg)

        lines.append(f"\n---\n**{passed}/{total} scenarios passed**")
        if passed < total:
            lines.append("\n⚠️  Some scenarios failed — check results above.")
        return "\n".join(lines)

    @app.tool(is_local=True)
    def run_sdk_scenarios() -> str:
        """Run SDK surface-area scenarios only."""
        memory = app.memory
        ts = _ts()
        results = (
            _sdk_semantic(memory, ts)
            + _sdk_episodic(memory, ts)
            + _sdk_taxonomic(memory, ts)
            + _sdk_procedural(memory, ts)
            + _sdk_context(memory, ts)
            + _sdk_context_isolation(memory, ts)
        )
        passed = sum(1 for _, ok in results if ok)
        return "\n".join(r for r, _ in results) + f"\n\n{passed}/{len(results)} passed"

    @app.tool(is_local=True)
    def run_context_isolation_scenarios() -> str:
        """Run build_context cross-user isolation scenarios (AP-184)."""
        results = _sdk_context_isolation(app.memory, _ts())
        passed = sum(1 for _, ok in results if ok)
        return "\n".join(r for r, _ in results) + f"\n\n{passed}/{len(results)} passed"

    @app.tool(is_local=True)
    def run_policy_scenarios() -> str:
        """Run OE access-policy enforcement scenarios only."""
        results = _policy_scenarios(_ts())
        passed = sum(1 for _, ok in results if ok)
        return "\n".join(r for r, _ in results) + f"\n\n{passed}/{len(results)} passed"

    return {
        "run_all_scenarios": run_all_scenarios,
        "run_sdk_scenarios": run_sdk_scenarios,
        "run_policy_scenarios": run_policy_scenarios,
        "run_context_isolation_scenarios": run_context_isolation_scenarios,
    }
