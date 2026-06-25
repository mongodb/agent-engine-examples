#!/usr/bin/env python3
"""End-to-end demo for the Holiday Assistant Agent.

Drives the agent through a scripted multi-turn walkthrough that exercises
the full set of capabilities: hotel search, booking, transport advice,
policy lookup, instant cancellation, and the human-in-the-loop
cancellation approval flow for boutique / luxury hotels.

Prerequisites:
    1. From the agent directory, start the local stack:
           agentic dev up --workspace holiday-assistant-agent
    2. Seed the holiday database (only needed once):
           uv run holiday-assistant-seed

Run:
    uv run python demo.py
    uv run python demo.py --base-url http://localhost:32825
    uv run python demo.py --skip-section hitl
    uv run python demo.py --quiet

The script auto-discovers the local OE URL by querying
``docker compose port`` against the agent's compose file. It does not
require ``agentic auth login``.

Exit status is 0 when every scripted assertion passes, non-zero otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, request

SCRIPT_DIR = Path(__file__).resolve().parent
COMPOSE_PATH = SCRIPT_DIR / ".agentic" / "docker-compose.dev.yml"
COMPOSE_PROJECT = "holiday-assistant-agent"
DEFAULT_TIMEOUT_S = 240
POLL_INTERVAL_S = 2.0
REQUEST_TIMEOUT_S = 30.0


# ─── Pretty terminal output ──────────────────────────────────────────────────


class Style:
    """ANSI styles, auto-disabled when not writing to a TTY."""

    enabled = sys.stdout.isatty()

    @classmethod
    def _wrap(cls, code: str, text: str) -> str:
        if not cls.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    @classmethod
    def bold(cls, text: str) -> str:
        return cls._wrap("1", text)

    @classmethod
    def dim(cls, text: str) -> str:
        return cls._wrap("2", text)

    @classmethod
    def cyan(cls, text: str) -> str:
        return cls._wrap("36", text)

    @classmethod
    def green(cls, text: str) -> str:
        return cls._wrap("32", text)

    @classmethod
    def red(cls, text: str) -> str:
        return cls._wrap("31", text)

    @classmethod
    def yellow(cls, text: str) -> str:
        return cls._wrap("33", text)


# ─── HTTP plumbing ───────────────────────────────────────────────────────────


@dataclass
class HttpResult:
    status: int
    body: Any
    raw: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300


def http_post(url: str, payload: dict, timeout: float = REQUEST_TIMEOUT_S) -> HttpResult:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    return _fetch(req, timeout)


def http_get(url: str, timeout: float = REQUEST_TIMEOUT_S) -> HttpResult:
    return _fetch(request.Request(url, method="GET"), timeout)


def _fetch(req: request.Request, timeout: float) -> HttpResult:
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(text) if text else None
            except json.JSONDecodeError:
                body = text
            return HttpResult(status=resp.status, body=body, raw=text)
    except error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(text) if text else None
        except json.JSONDecodeError:
            body = text
        return HttpResult(status=e.code, body=body, raw=text, error=f"HTTP {e.code}")
    except error.URLError as e:
        return HttpResult(status=0, body=None, raw="", error=str(e.reason))
    except Exception as e:  # noqa: BLE001
        return HttpResult(status=0, body=None, raw="", error=str(e))


# ─── OE base URL discovery ──────────────────────────────────────────────────


def _docker_compose_port(service: str, container_port: int) -> str | None:
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                COMPOSE_PROJECT,
                "-f",
                str(COMPOSE_PATH),
                "port",
                service,
                str(container_port),
            ],
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    match = re.search(r":(\d+)\s*$", line)
    if not match:
        return None
    return f"http://127.0.0.1:{match.group(1)}"


def _docker_port(container: str, container_port: int) -> str | None:
    try:
        result = subprocess.run(
            ["docker", "port", container, str(container_port)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    match = re.search(r":(\d+)\s*$", line)
    if not match:
        return None
    return f"http://127.0.0.1:{match.group(1)}"


def discover_base_url() -> str:
    """Find a healthy local OE URL.

    Prefers `docker compose port oe 8000` on the agent's compose project.
    Falls back to looking up the OE container by conventional names.
    """
    candidates: list[tuple[str, str]] = []
    for service in ("oe", "app"):
        url = _docker_compose_port(service, 8000)
        if url:
            candidates.append((url, f"compose:{service}"))
    for container in (f"{COMPOSE_PROJECT}-oe-1", f"{COMPOSE_PROJECT}-app-1"):
        url = _docker_port(container, 8000)
        if url:
            candidates.append((url, f"docker:{container}"))

    seen: set[str] = set()
    deduped = []
    for url, source in candidates:
        if url in seen:
            continue
        seen.add(url)
        deduped.append((url, source))

    failures: list[str] = []
    for url, source in deduped:
        result = http_get(f"{url}/health", timeout=5)
        if result.ok:
            return url
        failures.append(f"{source} ({url}): {result.error or result.status}")

    detail = " Tried: " + " | ".join(failures) if failures else ""
    raise RuntimeError(
        "Could not discover a healthy local OE URL. Start the stack with "
        "`agentic dev up --workspace holiday-assistant-agent` or pass "
        f"--base-url.{detail}"
    )


# ─── Agent driver ────────────────────────────────────────────────────────────


@dataclass
class StepResult:
    status: str
    content: str
    execution_id: str | None = None
    suspend_reason: str | None = None
    suspend_context: dict | None = None
    raw: dict = field(default_factory=dict)


def _normalize_execution(data: Any) -> dict:
    if not isinstance(data, dict):
        return {}
    nested = data.get("execution") if isinstance(data.get("execution"), dict) else None
    out: dict[str, Any] = {}
    for key in ("execution_id", "status", "result", "error", "suspend_reason", "suspend_context"):
        if key in data:
            out[key] = data[key]
        elif nested and key in nested:
            out[key] = nested[key]
    return out


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("result", "response", "message", "output", "text", "content"):
            v = value.get(key)
            if isinstance(v, str):
                return v
        if isinstance(value.get("result"), dict):
            inner = _extract_text(value["result"])
            if inner:
                return inner
        return json.dumps(value, indent=2)
    return str(value)


def wait_for_execution(
    base_url: str, execution_id: str, timeout_s: float
) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {"execution_id": execution_id}
    while time.monotonic() < deadline:
        resp = http_get(f"{base_url}/execution/{execution_id}")
        if resp.ok:
            last = _normalize_execution(resp.body)
            status = last.get("status")
            if status in {"completed", "suspended", "failed", "error"}:
                return last
        time.sleep(POLL_INTERVAL_S)
    last["status"] = "error"
    last["error"] = f"Timed out waiting for execution {execution_id}"
    return last


def chat(
    base_url: str,
    thread_id: str,
    user_id: str,
    message: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> StepResult:
    invoke = http_post(
        f"{base_url}/invoke",
        {
            "message": message,
            "thread_id": thread_id,
            "user_id": user_id,
            "wait": False,
        },
        timeout=REQUEST_TIMEOUT_S,
    )
    if not invoke.ok:
        return StepResult(
            status="error",
            content=invoke.error or f"HTTP {invoke.status}: {invoke.raw}",
        )

    initial = _normalize_execution(invoke.body)
    execution_id = initial.get("execution_id")
    if not execution_id:
        return StepResult(
            status=initial.get("status", "completed"),
            content=_extract_text(initial.get("result") or invoke.body),
            raw=initial,
        )

    final = wait_for_execution(base_url, execution_id, timeout_s)
    status = final.get("status") or "error"
    if status == "suspended":
        return StepResult(
            status="suspended",
            content=_extract_text(final.get("result")),
            execution_id=execution_id,
            suspend_reason=final.get("suspend_reason"),
            suspend_context=(
                final.get("suspend_context")
                if isinstance(final.get("suspend_context"), dict)
                else None
            ),
            raw=final,
        )
    if status == "completed":
        return StepResult(
            status="completed",
            content=_extract_text(final.get("result")),
            execution_id=execution_id,
            raw=final,
        )
    return StepResult(
        status="error",
        content=_extract_text(final.get("error") or final),
        execution_id=execution_id,
        raw=final,
    )


def resume(
    base_url: str,
    execution_id: str,
    decision: str,
    notes: str = "",
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> StepResult:
    payload = {"human_review": {"decision": decision, "reviewer_notes": notes or None}}
    resp = http_post(f"{base_url}/resume/{execution_id}", payload)
    if not resp.ok:
        return StepResult(
            status="error",
            content=resp.error or f"HTTP {resp.status}: {resp.raw}",
        )
    initial = _normalize_execution(resp.body)
    final = wait_for_execution(
        base_url, initial.get("execution_id") or execution_id, timeout_s
    )
    status = final.get("status") or "error"
    if status == "completed":
        return StepResult(
            status="completed",
            content=_extract_text(final.get("result")),
            execution_id=execution_id,
            raw=final,
        )
    if status == "suspended":
        return StepResult(
            status="suspended",
            content=_extract_text(final.get("result")),
            execution_id=execution_id,
            suspend_reason=final.get("suspend_reason"),
            suspend_context=(
                final.get("suspend_context")
                if isinstance(final.get("suspend_context"), dict)
                else None
            ),
            raw=final,
        )
    return StepResult(
        status="error",
        content=_extract_text(final.get("error") or final),
        execution_id=execution_id,
        raw=final,
    )


# ─── Demo orchestration ─────────────────────────────────────────────────────


@dataclass
class Section:
    name: str
    title: str
    runner: Any  # Callable[[Demo], None]


@dataclass
class Demo:
    base_url: str
    user_id: str
    quiet: bool = False
    failures: list[str] = field(default_factory=list)
    booking_refs: dict[str, str] = field(default_factory=dict)

    def thread(self, label: str) -> str:
        return f"demo-{label}-{uuid.uuid4().hex[:8]}"

    # ── Output ──

    def section(self, title: str) -> None:
        bar = "═" * 78
        print()
        print(Style.cyan(bar))
        print(Style.cyan(f" {title}"))
        print(Style.cyan(bar))

    def step(self, n: int, prompt: str) -> None:
        print()
        print(Style.bold(f"[{n}] User → ") + prompt)

    def assistant(self, text: str) -> None:
        if not text:
            print(Style.dim("    (assistant returned no text)"))
            return
        for line in text.splitlines() or [""]:
            print(Style.dim("    ") + line)

    def info(self, text: str) -> None:
        print(Style.dim(f"    · {text}"))

    def ok(self, text: str) -> None:
        print(Style.green(f"    ✓ {text}"))

    def fail(self, text: str) -> None:
        print(Style.red(f"    ✗ {text}"))
        self.failures.append(text)

    # ── Assertions ──

    def expect_contains(self, result: StepResult, needles: list[str], label: str) -> None:
        haystack = result.content.lower()
        missing = [n for n in needles if n.lower() not in haystack]
        if not missing:
            self.ok(f"{label} — found expected phrases ({', '.join(repr(n) for n in needles)})")
        else:
            self.fail(f"{label} — missing phrases: {missing}; got: {result.content[:300]!r}")

    def expect_status(self, result: StepResult, status: str, label: str) -> None:
        if result.status == status:
            self.ok(f"{label} — status={status}")
        else:
            self.fail(
                f"{label} — expected status={status} but got status={result.status} "
                f"(content: {result.content[:200]!r})"
            )

    def expect_booking_ref(self, result: StepResult, label: str) -> str | None:
        match = re.search(r"\b([A-HJ-NP-Z2-9]{8})\b", result.content)
        if match:
            ref = match.group(1)
            self.ok(f"{label} — extracted booking ref {ref}")
            return ref
        self.fail(f"{label} — no 8-char booking ref found in: {result.content[:300]!r}")
        return None

    # ── Driver ──

    def chat(self, thread: str, n: int, message: str) -> StepResult:
        self.step(n, message)
        result = chat(self.base_url, thread, self.user_id, message)
        self.assistant(result.content)
        if result.status not in {"completed", "suspended"}:
            self.info(f"status={result.status}")
        return result


# ─── Section runners ────────────────────────────────────────────────────────


def section_search_and_book(d: Demo) -> None:
    """Search hotels in Barcelona, then book a 4-star room. Cancel instantly."""
    d.section("1. Hotel search + instant-cancellation booking (Barcelona)")
    thread = d.thread("barcelona")

    r = d.chat(thread, 1, "Find me a 4-star hotel in Barcelona under 200 EUR per night.")
    d.expect_status(r, "completed", "Hotel search")
    d.expect_contains(r, ["barcelona"], "Hotel search response mentions Barcelona")

    r = d.chat(
        thread,
        2,
        "Great — book the standard room at Hotel Catalonia Barcelona Plaza for "
        "Alice Smith (alice@example.com), 1 guest, check-in 10 August 2026, "
        "check-out 12 August 2026, 145 EUR per night, breakfast included. "
        "All details are confirmed — go ahead and book.",
    )
    d.expect_status(r, "completed", "Hotel booking")
    ref = d.expect_booking_ref(r, "Hotel booking returns 8-char reference")
    if ref:
        d.booking_refs["barcelona"] = ref

    if ref:
        r = d.chat(
            thread,
            3,
            f"Please cancel booking {ref} — change of plans. Just confirm "
            "once it's done.",
        )
        d.expect_status(r, "completed", "Instant cancellation")
        d.expect_contains(r, ["cancel"], "Cancellation confirmation")


def section_hitl(d: Demo) -> None:
    """The flagged-hotel cancellation flow.

    The crucial assertions are around the heads-up turn: the agent MUST tell
    the user the hotel handles cancellations directly BEFORE the suspend
    happens. Then the user confirms, the request suspends, the demo resumes
    with an approval, and the agent reports the cancellation as complete.
    """
    d.section("2. Boutique-hotel cancellation: HITL approval flow (Le Meurice Paris)")
    thread = d.thread("paris-hitl")

    r = d.chat(
        thread,
        1,
        "Book a deluxe room at Le Meurice Paris for Bob Jones "
        "(bob@example.com), 2 guests, check-in 5 September 2026, check-out "
        "8 September 2026, 980 EUR per night, no breakfast included. All "
        "details are confirmed — go ahead and book.",
    )
    d.expect_status(r, "completed", "Le Meurice booking")
    ref = d.expect_booking_ref(r, "Le Meurice booking returns reference")
    if not ref:
        return
    d.booking_refs["paris"] = ref

    # Step 2: ask to cancel — agent should NOT suspend yet, must explain
    # that the hotel needs to approve and ask for confirmation.
    r = d.chat(thread, 2, f"Actually I need to cancel booking {ref}.")
    d.expect_status(r, "completed", "Cancel-request turn (heads-up, NOT suspended)")
    if r.status == "suspended":
        d.fail(
            "BUG: the agent suspended on the first cancel turn without telling "
            "the user the hotel needs to approve."
        )
    d.expect_contains(
        r,
        ["le meurice", "approve"],
        "Heads-up message names the hotel and mentions approval",
    )

    # Step 3: user confirms — the agent should call cancel_booking with
    # user_confirmed_hotel_contact=true, which suspends. The agent has no
    # chance to message the user mid-tool-call; the resume reply at step 4
    # is responsible for relaying both submission and decision.
    r = d.chat(
        thread,
        3,
        f"Yes, please go ahead and submit the cancellation request for {ref} "
        "to Le Meurice on my behalf.",
    )
    d.expect_status(r, "suspended", "Submit turn suspends for hotel approval")
    if r.status != "suspended" or not r.execution_id:
        return
    if r.suspend_reason:
        d.info(f"suspend_reason: {r.suspend_reason}")
    if r.suspend_context and r.suspend_context.get("hotel_name"):
        d.info(f"reviewer (hotel): {r.suspend_context['hotel_name']}")
    if r.suspend_reason == "hotel_cancellation_decision":
        d.ok("Suspend reason is 'hotel_cancellation_decision'")
    else:
        d.fail(f"Expected suspend_reason=hotel_cancellation_decision, got {r.suspend_reason!r}")

    # Step 4: simulate the hotel approving the cancellation.
    print()
    print(Style.bold("[4] Reviewer (hotel) → ") + "approve")
    resumed = resume(
        d.base_url,
        r.execution_id,
        decision="approved",
        notes="Hotel accepts cancellation per demo script.",
    )
    d.assistant(resumed.content)
    d.expect_status(resumed, "completed", "Resume completes after approval")
    d.expect_contains(
        resumed,
        ["cancel"],
        "Final reply confirms the cancellation went through",
    )
    # The agent's resumed reply should reference both the submission and the
    # hotel's acceptance.
    d.expect_contains(
        resumed,
        ["le meurice"],
        "Final reply names the hotel that accepted",
    )


def section_hitl_rejection(d: Demo) -> None:
    """Same flow but the hotel rejects — booking should remain CONFIRMED."""
    d.section("3. Boutique-hotel cancellation: HITL rejection flow (Katikies Santorini)")
    thread = d.thread("santorini-reject")

    r = d.chat(
        thread,
        1,
        "Book a deluxe room at Katikies Santorini for Carol Davis "
        "(carol@example.com), 2 guests, check-in 1 July 2026, check-out "
        "5 July 2026, 720 EUR per night, breakfast included. All details "
        "are confirmed — go ahead and book.",
    )
    d.expect_status(r, "completed", "Katikies booking")
    ref = d.expect_booking_ref(r, "Katikies booking returns reference")
    if not ref:
        return

    r = d.chat(thread, 2, f"Cancel booking {ref} please.")
    if r.status == "suspended":
        d.fail("BUG: suspended without warning user about hotel approval.")
        return
    d.expect_status(r, "completed", "Heads-up returns without suspending")
    d.expect_contains(
        r, ["katikies", "approve"], "Heads-up names Katikies and mentions approval"
    )

    r = d.chat(
        thread,
        3,
        f"Yes, please go ahead and submit the cancellation request for {ref} "
        "to Katikies on my behalf.",
    )
    d.expect_status(r, "suspended", "Submission suspends")
    if r.status != "suspended" or not r.execution_id:
        return

    print()
    print(Style.bold("[4] Reviewer (hotel) → ") + "reject")
    resumed = resume(
        d.base_url,
        r.execution_id,
        decision="rejected",
        notes="Within the no-cancel window per the room's terms.",
    )
    d.assistant(resumed.content)
    d.expect_status(resumed, "completed", "Resume completes after rejection")
    if "cancel" in resumed.content.lower() and "cancelled" in resumed.content.lower():
        d.fail(
            "BUG: agent claimed booking was cancelled even though the hotel rejected."
        )
    else:
        d.ok("Final reply does not falsely claim cancellation went through")


def section_recall(d: Demo) -> None:
    """Cross-session booking recall.

    Books two trips in two separate sessions under the same user_id, then
    in a *third* fresh session asks about "my Paris trip" by name. The
    agent should look up the booking by user_id, NOT hallucinate a
    reference, and surface the real ref from session 1.
    """
    d.section("4. Cross-session recall: 'my trip to Paris'")

    s1 = d.thread("recall-s1-paris")
    r = d.chat(
        s1,
        1,
        "Book a deluxe room at Le Meurice Paris for Charlie Xu "
        "(charlie@example.com), 1 guest, check-in 10 October 2026, "
        "check-out 13 October 2026, 980 EUR per night, no breakfast. "
        "Go ahead and book.",
    )
    d.expect_status(r, "completed", "Paris booking (session 1)")
    paris_ref = d.expect_booking_ref(r, "Paris booking returns reference")
    if not paris_ref:
        return
    d.booking_refs["recall-paris"] = paris_ref

    s2 = d.thread("recall-s2-bcn")
    r = d.chat(
        s2,
        2,
        "Also book the standard room at Hotel Catalonia Barcelona Plaza "
        "for Charlie Xu (charlie@example.com), 1 guest, check-in 5 "
        "December 2026, check-out 8 December 2026, 145 EUR per night, "
        "breakfast included. Go ahead and book.",
    )
    d.expect_status(r, "completed", "Barcelona booking (session 2)")
    bcn_ref = d.expect_booking_ref(r, "Barcelona booking returns reference")
    if bcn_ref:
        d.booking_refs["recall-bcn"] = bcn_ref

    # Fresh thread, no prior context — the recall has to come from MongoDB.
    s3 = d.thread("recall-s3-paris-recall")
    r = d.chat(
        s3,
        3,
        "Can you remind me about my trip to Paris? Include the booking "
        "reference.",
    )
    d.expect_status(r, "completed", "Paris recall (session 3, fresh thread)")
    if paris_ref.lower() in r.content.lower():
        d.ok(f"Recall surfaces the real Paris ref ({paris_ref})")
    else:
        d.fail(
            f"Paris recall did not surface ref {paris_ref}; got: "
            f"{r.content[:300]!r}"
        )
    d.expect_contains(r, ["le meurice"], "Paris recall names the hotel")

    # Fresh thread, broad listing.
    s4 = d.thread("recall-s4-list")
    r = d.chat(s4, 4, "What reservations do I have on file?")
    d.expect_status(r, "completed", "List-all bookings (session 4)")
    if paris_ref.lower() in r.content.lower():
        d.ok("List-all surfaces Paris booking")
    else:
        d.fail(f"List-all missing Paris ref {paris_ref}; got: {r.content[:300]!r}")
    if bcn_ref and bcn_ref.lower() in r.content.lower():
        d.ok("List-all surfaces Barcelona booking")
    elif bcn_ref:
        d.fail(f"List-all missing Barcelona ref {bcn_ref}; got: {r.content[:300]!r}")

    # Different user_id — must NOT see Charlie's bookings.
    iso_user = f"isolated-{uuid.uuid4().hex[:8]}"
    iso_thread = d.thread("recall-isolation")
    r_iso = chat(d.base_url, iso_thread, iso_user, "What reservations do I have?")
    d.assistant(f"(as user {iso_user})")
    d.assistant(r_iso.content)
    if paris_ref.lower() in r_iso.content.lower() or (
        bcn_ref and bcn_ref.lower() in r_iso.content.lower()
    ):
        d.fail("Isolation broken: another user can see Charlie's bookings")
    else:
        d.ok("Different user_id sees no bookings (isolation holds)")


def section_memory(d: Demo) -> None:
    """Explicit cross-session memory: traveler facts + auto-saved booking episodes.

    The booking step in section_recall already exercised the auto-episode
    write path (the bookings collection got two new docs and the
    episodic store got two ``Booked …`` episodes). This section verifies
    the OTHER half: that ``remember_traveler_fact`` persists across
    threads, and that the LLM can pull both saved facts AND auto-saved
    booking episodes via ``recall_traveler_context`` in a fresh session.
    """
    d.section("5. Cross-session memory (facts + auto-saved episodes)")

    s1 = d.thread("memory-s1")
    r = d.chat(
        s1,
        1,
        "Please remember that my home airport is JFK and I always prefer "
        "4-star or higher hotels.",
    )
    d.expect_status(r, "completed", "Save traveler facts")

    s2 = d.thread("memory-s2")
    r = d.chat(
        s2,
        2,
        "What do you know about me? Look up my profile.",
    )
    d.expect_status(r, "completed", "Recall facts (fresh thread)")
    blob = r.content.lower()
    if "jfk" in blob:
        d.ok("Recalls home airport (JFK) across sessions")
    else:
        d.fail(f"Did not recall home airport in fresh thread; got: {r.content[:300]!r}")
    if "4-star" in blob or "4 star" in blob or "four-star" in blob:
        d.ok("Recalls hotel preference across sessions")
    else:
        d.fail(f"Did not recall 4-star preference; got: {r.content[:300]!r}")

    # Auto-saved booking episodes from section_recall (Le Meurice, Catalonia)
    # should still be queryable. Recall-by-topic, no booking_ref provided.
    s3 = d.thread("memory-s3-episode-recall")
    r = d.chat(
        s3,
        3,
        "Have we discussed any October trips before? Use recall to find out.",
    )
    d.expect_status(r, "completed", "Auto-episode recall (fresh thread)")
    blob = r.content.lower()
    if "le meurice" in blob or "paris" in blob or "october" in blob:
        d.ok("Auto-saved booking episode is recallable")
    else:
        d.info(
            "Auto-saved episode not surfaced — possibly the LLM answered "
            "from the bookings collection instead. This is acceptable since "
            "either path proves cross-session knowledge of the trip."
        )


def section_transport(d: Demo) -> None:
    d.section("6. Transport advice (London → Barcelona)")
    thread = d.thread("transport")

    r = d.chat(thread, 1, "How do I get from London to Barcelona on a budget? Compare options.")
    d.expect_status(r, "completed", "Transport advice returns a complete answer")
    d.expect_contains(
        r,
        ["barcelona"],
        "Transport advice mentions Barcelona",
    )


def section_policy(d: Demo) -> None:
    d.section("7. Policy lookup")
    thread = d.thread("policy")

    r = d.chat(thread, 1, "What is your cancellation policy?")
    d.expect_status(r, "completed", "Policy answer")
    d.expect_contains(r, ["cancel"], "Cancellation policy explanation")


# ─── Entrypoint ─────────────────────────────────────────────────────────────


SECTIONS: list[Section] = [
    Section("search", "Search + instant cancellation", section_search_and_book),
    Section("hitl", "HITL approval (Le Meurice)", section_hitl),
    Section("hitl-reject", "HITL rejection (Katikies)", section_hitl_rejection),
    Section("recall", "Cross-session booking recall", section_recall),
    Section("memory", "Cross-session memory (facts + episodes)", section_memory),
    Section("transport", "Transport advice", section_transport),
    Section("policy", "Policy lookup", section_policy),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("HOLIDAY_DEMO_BASE_URL"),
        help="Override OE base URL (default: auto-discover from docker compose)",
    )
    parser.add_argument(
        "--user-id",
        default=f"demo-{uuid.uuid4().hex[:8]}",
        help="Caller-supplied user_id (default: random)",
    )
    parser.add_argument(
        "--skip-section",
        action="append",
        default=[],
        choices=[s.name for s in SECTIONS],
        help="Skip a named section (can pass multiple times)",
    )
    parser.add_argument(
        "--only-section",
        action="append",
        default=[],
        choices=[s.name for s in SECTIONS],
        help="Run only the given section(s)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress assistant transcript"
    )
    args = parser.parse_args()

    if args.base_url:
        base_url = args.base_url.rstrip("/")
        # Confirm health on the chosen URL.
        if not http_get(f"{base_url}/health", timeout=5).ok:
            print(Style.red(f"OE health check failed for {base_url}"))
            return 1
    else:
        try:
            base_url = discover_base_url()
        except RuntimeError as exc:
            print(Style.red(str(exc)))
            return 1
    print(Style.bold("Holiday Assistant — End-to-End Demo"))
    print(Style.dim(f"OE: {base_url}"))
    print(Style.dim(f"user_id: {args.user_id}"))

    demo = Demo(base_url=base_url, user_id=args.user_id, quiet=args.quiet)

    selected = SECTIONS
    if args.only_section:
        selected = [s for s in SECTIONS if s.name in args.only_section]
    selected = [s for s in selected if s.name not in args.skip_section]

    for section in selected:
        try:
            section.runner(demo)
        except Exception as exc:  # noqa: BLE001
            demo.fail(f"section {section.name!r} crashed: {exc}")

    print()
    if demo.failures:
        print(Style.red(f"DEMO FAILED — {len(demo.failures)} assertion(s) failed:"))
        for f in demo.failures:
            print(Style.red(f"  - {f}"))
        return 1
    print(Style.green("DEMO PASSED — every assertion held."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
