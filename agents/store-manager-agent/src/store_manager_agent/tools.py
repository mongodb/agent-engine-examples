"""Tool registration for the Store Manager Copilot agent.

Tools span five domains:

* **Ops / reporting** — read the overnight operational report, store profile,
  and upcoming demand forecasts.
* **Inventory / reorder** — surface low stock, draft and place purchase orders.
  POs over the store's approval threshold go through a two-step human approval.
* **Expiry / markdown** — find near-expiry and dead inventory; apply markdowns
  or disposals directly and notify the floor team (no approval gate).
* **Planogram** — diff the observed shelf state against the approved planogram
  (pure data, no images) and dispatch a reset directly to the floor team (no
  approval gate).
* **Memory** — recall store context, look up domain definitions (taxonomic) and
  standard operating procedures (procedural), and persist store facts / shift
  summaries.

Only the purchase-order tool uses the two-step human-approval contract: the
first call returns ``status: needs_user_confirmation`` WITHOUT suspending, so
the agent must tell the manager before the pause; the second call
(``manager_confirmed=True``) returns a ``SuspendPayload`` and execution suspends
until a reviewer responds. After an ``approve`` decision the agent calls
``place_purchase_order_approved``, which is the only place that mutation is
written. Markdowns/disposals (``apply_markdown``) and planogram resets
(``apply_planogram_change``) apply immediately and the agent simply tells the
manager the floor team will be notified. ``purchase_orders`` and ``markdowns``
documents are stamped with the platform ``user_id`` at create time so a
manager's past decisions surface across sessions (the cross-session recall
payoff).
"""

from __future__ import annotations

import json
import logging
import math
import re
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from runner_shared.models import SuspendPayload  # type: ignore[import-untyped]

from store_manager_agent import mongo

logger = logging.getLogger(__name__)


# ─── constants (store_profile is the runtime source of truth; these are fallbacks)

PO_APPROVAL_THRESHOLD_USD = 500.0
NEAR_EXPIRY_DAYS = 3
DEAD_SKU_DAYS = 21
# Days-to-expiry → markdown depth. Evaluated most-aggressive first.
MARKDOWN_TIERS = [(1, 0.75), (3, 0.50)]
DEFAULT_CURRENCY = "USD"
TAXONOMIC_DOMAIN = "convenience_retail"
EPISODE_PARTICIPANTS = ["Store Manager", "Store Copilot"]
# A re-delivered or double-submitted approval can call a `*_approved` finalizer
# twice for the same logical decision (observed in telemetry: two executions
# resumed on the same markdown approval, writing two records). Finalizers are
# idempotent: before inserting they look for an identical record from the same
# user within this window and return that instead of writing a duplicate.
DEDUPE_WINDOW_SECONDS = 120


# ─── helpers ─────────────────────────────────────────────────────────────────


def _format_json(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _recent_duplicate(collection: Any, match: dict[str, Any]) -> dict[str, Any] | None:
    """Return a matching record created within the dedupe window, if any.

    Used by the ``*_approved`` finalizers so a re-delivered / double-submitted
    approval returns the existing record instead of writing a duplicate. The
    time bound keeps a *new, intentional* re-order of the same item later in the
    shift from being silently swallowed.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=DEDUPE_WINDOW_SECONDS)).isoformat()
    query = {**match, "created_at": {"$gte": cutoff}}
    return collection.find_one(query, projection={"_id": 0}, sort=[("created_at", -1)])


_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I to avoid confusion


def _generate_ref(collection: Any, field: str, prefix: str, length: int = 8) -> str:
    """Generate a unique ``PREFIX-XXXXXXXX`` reference for a collection."""
    for _ in range(10):
        ref = f"{prefix}-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(length))
        if not collection.find_one({field: ref}, projection={"_id": 1}):
            return ref
    return f"{prefix}-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(length))


def _current_user_id(app: Any) -> str | None:
    """Return the platform user_id for the current execution.

    Wraps ``app.get_current_user_id`` defensively because it raises rather than
    returning ``None`` when called outside an execution context (e.g. tests).
    """
    try:
        return app.get_current_user_id()
    except Exception:  # noqa: BLE001
        return None


def _safe_save_episode(
    app: Any,
    *,
    title: str,
    content: str,
    tags: list[str],
    user_id: str | None,
) -> None:
    """Best-effort write of an episodic memory for a decision event.

    Mutation tools call this after a successful PO / markdown so the manager's
    decision history accumulates deterministically rather than depending on the
    LLM remembering to call ``save_shift_summary``. Failures (memory disabled,
    episodic store unavailable) are logged and swallowed so they never break
    the operational flow itself.
    """
    if not user_id:
        return
    try:
        memory = getattr(app, "memory", None)
        if memory is None:
            return
        memory.save_episode(
            title=title,
            content=content,
            summary=content,
            participants=EPISODE_PARTICIPANTS,
            tags=tags,
            user_id=user_id,
            visibility="private",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Auto-episode save skipped (%s)", exc)


def _store_profile() -> dict[str, Any]:
    return mongo.store_profile_collection().find_one({}, projection={"_id": 0}) or {}


def _po_threshold() -> float:
    val = _store_profile().get("po_approval_threshold_usd")
    return float(val) if val else PO_APPROVAL_THRESHOLD_USD


def _near_expiry_days() -> int:
    val = _store_profile().get("near_expiry_days")
    return int(val) if val else NEAR_EXPIRY_DAYS


def _dead_sku_days() -> int:
    val = _store_profile().get("dead_sku_days")
    return int(val) if val else DEAD_SKU_DAYS


def _get_sku_doc(sku: str) -> dict[str, Any] | None:
    """Resolve a SKU leniently so the model self-corrects from a fuzzy id.

    Tries, in order: exact SKU (case-insensitive) → SKU substring → name
    substring. The fallbacks matter because the LLM sometimes passes a
    paraphrased id ("water") or even a hallucinated number; a name/substring
    hit recovers the real document instead of failing the turn.
    """
    if not sku:
        return None
    coll = mongo.skus_collection()
    needle = sku.strip()
    # 1) exact SKU match
    doc = coll.find_one(
        {"sku": {"$regex": f"^{re.escape(needle)}$", "$options": "i"}}, projection={"_id": 0}
    )
    if doc:
        return mongo.resolve_dates(doc)
    # 2) SKU substring, then 3) name substring
    for field in ("sku", "name"):
        doc = coll.find_one(
            {field: {"$regex": re.escape(needle), "$options": "i"}}, projection={"_id": 0}
        )
        if doc:
            return mongo.resolve_dates(doc)
    return None


def _sku_not_found(sku: str) -> str:
    """A not-found payload that lists the valid SKUs so the model can retry."""
    catalog = [
        {"sku": d.get("sku"), "name": d.get("name")}
        for d in mongo.find_docs(mongo.skus_collection())
    ]
    return _format_json(
        {
            "status": "retry_with_valid_sku",
            "error": f"No SKU found matching {sku!r}.",
            "hint": (
                "Do NOT ask the manager for the SKU and do NOT invent one. Pick "
                "the matching SKU from valid_skus below and immediately re-call "
                "the SAME tool with that exact `sku` value and the same other "
                "arguments."
            ),
            "valid_skus": catalog,
        }
    )


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except Exception:  # noqa: BLE001
        return None


def _days_until(value: Any) -> int | None:
    d = _parse_date(value)
    if d is None:
        return None
    return (d - _today()).days


def _forecast_multiplier(sku: str) -> tuple[float, dict[str, Any] | None]:
    """Largest upcoming demand multiplier for a SKU, plus the driving event."""
    best = 1.0
    driver: dict[str, Any] | None = None
    for event in mongo.find_docs(mongo.forecast_events_collection()):
        mult = (event.get("demand_multiplier") or {}).get(sku)
        if mult and float(mult) > best:
            best = float(mult)
            driver = event
    return best, driver


def _recommended_cases(sku_doc: dict[str, Any]) -> int:
    """Cases needed to reach (forecast-adjusted) par level from on-hand."""
    par = float(sku_doc.get("par_level", 0) or 0)
    on_hand = float(sku_doc.get("on_hand", 0) or 0)
    mult, _ = _forecast_multiplier(sku_doc.get("sku", ""))
    return max(0, math.ceil(par * mult - on_hand))


def _suggested_markdown(sku_doc: dict[str, Any]) -> dict[str, Any]:
    """Suggested markdown depth + price for a perishable, by days-to-expiry."""
    retail = float(sku_doc.get("retail_price", 0) or 0)
    days = _days_until(sku_doc.get("expiry_date"))
    if days is None:
        return {"recommended_action": "review", "days_to_expiry": None}
    if days < 0:
        return {
            "recommended_action": "disposal",
            "days_to_expiry": days,
            "note": "Already expired — recommend disposal per markdown SOP.",
        }
    pct = 0.0
    for limit, tier_pct in MARKDOWN_TIERS:  # most-aggressive first
        if days <= limit:
            pct = tier_pct
            break
    to_price = round(retail * (1 - pct), 2) if pct else retail
    return {
        "recommended_action": "markdown",
        "days_to_expiry": days,
        "markdown_pct": round(pct * 100),
        "from_price": retail,
        "suggested_to_price": to_price,
    }


def _save_pending_basket(
    user_id: str | None,
    items: list[dict[str, Any]],
    reason: str,
    *,
    submitted: bool,
) -> None:
    """Stash the validated over-limit basket the manager was shown, keyed to the
    manager. ``submitted`` records whether this basket has actually been routed
    for approval (i.e. the tool suspended), which the finalizer gate requires.

    Two purposes:

    1. **Faithful basket** — smaller models often drop or zero ``qty_cases`` when
       they re-call ``place_purchase_order`` / ``place_purchase_order_approved``.
       Persisting the validated lines lets the submit/finalize recover the real
       basket instead of trusting the model to re-type it.
    2. **Approval gate** — ``submitted=True`` is written only at the suspend
       step, so ``place_purchase_order_approved`` can REFUSE to place anything
       that wasn't actually submitted for approval (no self-approval).

    One pending basket per manager — the latest heads-up / submit wins. A Mongo
    write here is a committed document, fully independent of the LangGraph
    interrupt, so it survives the suspend and is readable on resume.
    """
    if not user_id:
        return
    try:
        mongo.pending_purchase_orders_collection().replace_one(
            {"user_id": user_id},
            {
                "user_id": user_id,
                "items": items,
                "reason": reason,
                "submitted_for_approval": submitted,
                "created_at": _now_iso(),
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not stash pending basket: %s", exc)


def _load_pending_basket(
    user_id: str | None,
) -> tuple[list[dict[str, Any]], str, bool]:
    """Return ``(items, reason, submitted_for_approval)`` for this manager's
    stashed basket, or ``([], "", False)`` if none. Best-effort; never raises
    into the tool flow."""
    if not user_id:
        return [], "", False
    try:
        doc = mongo.pending_purchase_orders_collection().find_one(
            {"user_id": user_id}, projection={"_id": 0}
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load pending basket: %s", exc)
        return [], "", False
    if not doc:
        return [], "", False
    return (
        list(doc.get("items") or []),
        doc.get("reason") or "",
        bool(doc.get("submitted_for_approval")),
    )


def _clear_pending_basket(user_id: str | None) -> None:
    """Drop this manager's stashed basket once the PO is placed (or abandoned)."""
    if not user_id:
        return
    try:
        mongo.pending_purchase_orders_collection().delete_many({"user_id": user_id})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not clear pending basket: %s", exc)


def _purchase_orders_for_user(user_id: str | None, limit: int = 20) -> list[dict[str, Any]]:
    if not user_id:
        return []
    cursor = (
        mongo.purchase_orders_collection()
        .find({"user_id": user_id}, projection={"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    return list(cursor)


def _markdowns_for_user(user_id: str | None, limit: int = 20) -> list[dict[str, Any]]:
    if not user_id:
        return []
    cursor = (
        mongo.markdowns_collection()
        .find({"user_id": user_id}, projection={"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    return list(cursor)


def _needs_confirmation(**fields: Any) -> str:
    return _format_json({"status": "needs_user_confirmation", **fields})


def _slug_procedure(name: str) -> str:
    """Normalize a procedure name to the memory server's required pattern
    ``^[a-z0-9]+(-[a-z0-9]+)*$`` — lowercase, non-alphanumerics collapsed to
    single hyphens. The server 500s on underscores/uppercase, so every
    procedure name (SOPs and per-manager routines) MUST pass through this."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "procedure"


def _routine_name(user_id: str | None) -> str:
    """Per-manager morning-rundown routine name (procedural memory), normalized
    to the server's required lowercase-hyphen pattern."""
    return f"{_slug_procedure(user_id or 'anon')}-morning-rundown"


def _resolve_basket(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate a list of {sku, qty_cases} order lines against the catalog.

    Returns (resolved_lines, unknown_skus). Every resolved line carries the
    canonical SKU, name, unit_cost and extended cost. Unknown SKUs are NOT
    silently dropped — the caller rejects the whole basket so the model can't
    order imaginary products.

    Quantity self-heal: if a line names a VALID catalog SKU but the quantity is
    missing or non-positive (smaller models routinely drop ``qty_cases`` when
    re-typing a basket), fall back to the SOP-recommended reorder quantity
    (par x forecast - on_hand) instead of rejecting. The line is marked with
    ``qty_autofilled: True`` so the tool can be transparent about it. A SKU whose
    SOP quantity also comes out 0 (already at/above par) is left as unknown so it
    isn't silently ordered.
    """
    resolved: list[dict[str, Any]] = []
    unknown: list[str] = []
    for item in items or []:
        sku = (item.get("sku") or "").strip()
        try:
            qty = int(item.get("qty_cases") or 0)
        except (TypeError, ValueError):
            qty = 0
        sku_doc = _get_sku_doc(sku) if sku else None
        if not sku_doc:
            unknown.append(sku or "(blank)")
            continue
        autofilled = False
        if qty <= 0:
            qty = _recommended_cases(sku_doc)
            autofilled = True
        if qty <= 0:
            unknown.append(f"{sku_doc.get('sku')} (qty must be positive)")
            continue
        unit_cost = float(sku_doc.get("unit_cost", 0) or 0)
        line = {
            "sku": sku_doc.get("sku"),
            "name": sku_doc.get("name"),
            "qty_cases": qty,
            "unit_cost": unit_cost,
            "extended_cost_usd": round(qty * unit_cost, 2),
        }
        if autofilled:
            line["qty_autofilled"] = True
        resolved.append(line)
    return resolved, unknown


def _resolve_markdown_basket(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate a list of markdown/disposal lines against the catalog.

    Each input item is ``{"sku", "action", "to_price"?, "reason"?}`` where
    ``action`` is "markdown" or "disposal". Returns (resolved_lines, problems).
    Resolved lines carry canonical sku/name, from/to price, pct, and reason.
    Unknown SKUs or bad actions reject the whole basket (no imaginary items).
    """
    resolved: list[dict[str, Any]] = []
    problems: list[str] = []
    for item in items or []:
        sku = (item.get("sku") or "").strip()
        action = (item.get("action") or "").lower().strip()
        if action not in {"markdown", "disposal"}:
            problems.append(f"{sku or '(blank)'} (action must be markdown/disposal)")
            continue
        sku_doc = _get_sku_doc(sku) if sku else None
        if not sku_doc:
            problems.append(sku or "(blank)")
            continue
        retail = float(sku_doc.get("retail_price", 0) or 0)
        if action == "markdown":
            to_price = item.get("to_price")
            try:
                to_price = float(to_price) if to_price else 0.0
            except (TypeError, ValueError):
                to_price = 0.0
            if to_price <= 0:
                to_price = float(_suggested_markdown(sku_doc).get("suggested_to_price") or retail)
            pct = round((1 - (to_price / retail)) * 100) if retail else 0
        else:
            to_price, pct = 0.0, 100
        resolved.append(
            {
                "sku": sku_doc.get("sku"),
                "name": sku_doc.get("name"),
                "action": action,
                "from_price": retail,
                "to_price": to_price,
                "pct": pct,
                "reason": item.get("reason", ""),
            }
        )
    return resolved, problems


# ─── registration ────────────────────────────────────────────────────────────


def register(app: Any) -> None:
    """Attach ops / inventory / expiry / planogram / memory tools to the App."""

    # ── OPS / REPORTING ───────────────────────────────────────────────────────

    @app.tool(is_local=False)
    def get_store_profile() -> str:
        """Return the store profile: format, demand traits, and the operating
        thresholds (PO approval limit, near-expiry window, dead-SKU window).

        Call this at the start of a morning rundown to ground your advice in
        this specific store's characteristics.
        """
        profile = _store_profile()
        if not profile:
            return "No store profile found. Has the store data been seeded?"
        return _format_json(profile)

    @app.tool(is_local=False)
    def get_overnight_report(report_date: str = "") -> str:
        """Return the overnight operational report — sales, transactions, waste,
        register voids, out-of-stocks, and equipment alarms.

        Args:
            report_date: Optional ISO date (YYYY-MM-DD). Defaults to the most
                recent report on file.
        """
        # Reports carry a relative date offset resolved at read time, so the
        # ops-report date is always "today" however long after seeding the demo
        # runs. find_docs() applies that resolution.
        reports = mongo.find_docs(mongo.ops_reports_collection())
        if not reports:
            return "No operational report found. Has the store data been seeded?"
        if report_date:
            doc = next((r for r in reports if r.get("report_date") == report_date), None)
            if not doc:
                return f"No operational report found for {report_date}."
        else:
            # Most recent by (resolved) report_date.
            doc = max(reports, key=lambda r: r.get("report_date") or "")
        return _format_json(doc)

    @app.tool(is_local=False)
    def get_forecast_events(days_ahead: int = 7) -> str:
        """Return upcoming demand-driving events (e.g. a forecast heatwave) with
        their per-SKU demand multipliers. Use this to anticipate reorder needs.
        """
        events = mongo.find_docs(mongo.forecast_events_collection())
        upcoming = []
        for e in events:
            days = _days_until(e.get("date"))
            if days is None or 0 <= days <= days_ahead:
                upcoming.append({**e, "days_away": days})
        if not upcoming:
            return _format_json({"events": [], "message": "No upcoming demand events on file."})
        return _format_json({"events": upcoming})

    @app.tool(is_local=False)
    def get_weather_forecast(days_ahead: int = 4) -> str:
        """Return the local weather forecast for the store, including any heat
        advisories.

        Weather drives convenience-store demand: hot days spike bottled water,
        energy drinks, and frozen/slush beverages. Use this when recommending
        what to prioritize restocking — especially for out-of-stock items — and
        cite the forecast as the justification (e.g. "a 99°F heatwave hits in
        two days, so I'd prioritize the bottled-water restock").
        """
        doc = next(iter(mongo.find_docs(mongo.weather_forecast_collection())), None)
        if not doc:
            return _format_json(
                {"forecast": None, "message": "No weather forecast on file."}
            )
        days = [d for d in doc.get("days", []) if (_days_until(d.get("date")) or 0) <= days_ahead]
        heat = [d for d in days if d.get("heat_advisory")]
        return _format_json(
            {
                "location": doc.get("location"),
                "summary": doc.get("summary"),
                "heat_advisory_upcoming": bool(heat),
                "days": days,
            }
        )

    # ── INVENTORY / REORDER ───────────────────────────────────────────────────

    @app.tool(is_local=False)
    def get_low_stock(include_forecast: bool = True) -> str:
        """List SKUs at or below their reorder point, with a recommended order
        quantity (in cases). When ``include_forecast`` is true the recommended
        quantity is scaled up by any upcoming demand event (e.g. a heatwave).

        A "reorder point" is the on-hand level that should trigger replenishment
        (see `explain_term`). Use this to identify what to reorder.
        """
        low = []
        for sku_doc in mongo.find_docs(mongo.skus_collection()):
            on_hand = float(sku_doc.get("on_hand", 0) or 0)
            reorder_point = float(sku_doc.get("reorder_point", 0) or 0)
            if on_hand > reorder_point:
                continue
            mult, driver = _forecast_multiplier(sku_doc.get("sku", "")) if include_forecast else (1.0, None)
            recommended = _recommended_cases(sku_doc) if include_forecast else max(
                0, math.ceil(float(sku_doc.get("par_level", 0) or 0) - on_hand)
            )
            unit_cost = float(sku_doc.get("unit_cost", 0) or 0)
            low.append(
                {
                    "sku": sku_doc.get("sku"),
                    "name": sku_doc.get("name"),
                    "on_hand": on_hand,
                    "reorder_point": reorder_point,
                    "par_level": sku_doc.get("par_level"),
                    "unit_cost": unit_cost,
                    "currency": sku_doc.get("currency", DEFAULT_CURRENCY),
                    "forecast_multiplier": mult,
                    "forecast_event": (driver or {}).get("event") if driver else None,
                    "recommended_order_cases": recommended,
                    "estimated_cost_usd": round(recommended * unit_cost, 2),
                }
            )
        if not low:
            return _format_json({"low_stock": [], "message": "All SKUs are above reorder point."})
        return _format_json({"low_stock": low, "po_approval_threshold_usd": _po_threshold()})

    @app.tool(is_local=False)
    def get_sku(sku: str) -> str:
        """Return full inventory detail for a single SKU."""
        doc = _get_sku_doc(sku)
        if not doc:
            return _sku_not_found(sku)
        return _format_json(doc)

    @app.tool(is_local=True)
    def place_purchase_order(
        items: list[dict[str, Any]],
        reason: str = "",
        manager_confirmed: bool = False,
    ) -> str:
        """Place a replenishment purchase order for ONE OR MORE items in a single
        basket. This is the only reorder tool — always pass every item the
        manager wants to order in one call; never call it once per item.

        ``items`` is a list of ``{"sku": "<exact SKU>", "qty_cases": <int>}``.
        SKUs must be exact catalog codes from ``get_low_stock`` /
        ``get_overnight_report`` — unknown SKUs are rejected (the whole basket
        is refused so nothing imaginary is ordered).

        The basket total decides the flow:

        * **Under** the store's approval threshold → placed immediately as one PO.
        * **At or above** the threshold → two-step approval:
          1. First call (``manager_confirmed=False``, default) returns
             ``status: needs_user_confirmation`` and does NOT suspend — tell the
             manager the basket total is over the limit and ask them to approve.
          2. After they agree, call again with the SAME ``items`` and
             ``manager_confirmed=True``; this suspends ONCE for the whole basket.
             After an ``approve`` decision, call ``place_purchase_order_approved``
             (no args needed — it places the approved basket).

        Args:
            items: List of {"sku", "qty_cases"} order lines.
            reason: Short justification (e.g. "Heatwave restock").
            manager_confirmed: True only after the manager agreed to submit an
                over-threshold basket for approval.
        """
        user_id = _current_user_id(app)
        resolved, unknown = _resolve_basket(items)

        # On the confirm call, the basket the manager actually saw and approved
        # is the one we stashed at the heads-up step — so prefer it. Smaller
        # models often re-type the basket with a dropped/zeroed quantity, which
        # would otherwise resolve (via qty self-heal) to a DIFFERENT, smaller
        # basket than what was approved and could slip under the limit. Using the
        # stash keeps the submitted basket faithful to what was shown.
        if manager_confirmed:
            stashed, stashed_reason, _ = _load_pending_basket(user_id)
            stashed_resolved, stashed_unknown = _resolve_basket(stashed)
            if stashed_resolved:
                resolved, unknown = stashed_resolved, stashed_unknown
                if not reason:
                    reason = stashed_reason

        if unknown and not resolved:
            catalog = [
                {"sku": d.get("sku"), "name": d.get("name")}
                for d in mongo.find_docs(mongo.skus_collection())
            ]
            return _format_json(
                {
                    "status": "error",
                    "error": f"Unknown or invalid SKUs in the order: {unknown}.",
                    "hint": (
                        "Order ONLY exact catalog SKUs returned by get_low_stock "
                        "or get_overnight_report. Do not invent SKUs. Re-call "
                        "place_purchase_order with corrected `items`."
                    ),
                    "valid_skus": catalog,
                }
            )
        if not resolved:
            return _format_json({"status": "error", "error": "No valid items to order."})

        basket_total = round(sum(line["extended_cost_usd"] for line in resolved), 2)
        threshold = _po_threshold()
        lines_desc = ", ".join(f"{ln['qty_cases']}x {ln['name']}" for ln in resolved)

        if basket_total < threshold:
            # Whole basket under the limit — place immediately as one PO.
            _clear_pending_basket(user_id)
            return _place_po_basket(app, resolved, basket_total, reason, requires_approval=False)

        if not manager_confirmed:
            # Stash the validated basket so the confirm/finalize step can recover
            # it even if the model garbles `items` on the next call. Not yet
            # submitted for approval — the finalizer gate must still see a suspend.
            _save_pending_basket(user_id, resolved, reason, submitted=False)
            return _needs_confirmation(
                items=resolved,
                basket_total_usd=basket_total,
                over_threshold=True,
                threshold_usd=threshold,
                next_action=(
                    "DO NOT call place_purchase_order again yet. First tell the "
                    f"manager this order totals ${basket_total:.2f}, over the "
                    f"${threshold:.0f} approval limit, and ask them to approve it. "
                    "Only after they say yes, call place_purchase_order again with "
                    "the SAME items and manager_confirmed=true."
                ),
                suggested_message_to_user=(
                    f"This order ({lines_desc}) comes to ${basket_total:.2f}, over "
                    f"the ${threshold:.0f} approval limit. Want me to submit it for "
                    "your approval?"
                ),
            )

        # Over the limit and confirmed — suspend ONCE for the whole basket. The
        # validated basket rides in suspend_context (which the runtime persists
        # across the suspend). We ALSO refresh the Mongo stash and mark it
        # submitted_for_approval=True: this is the ONLY place that flag is set, so
        # place_purchase_order_approved can both recover the basket (if the model
        # drops `items` on finalize) AND verify a suspend actually happened
        # before it places anything. On approval the agent calls
        # place_purchase_order_approved with these same items.
        _save_pending_basket(user_id, resolved, reason, submitted=True)
        task_id = f"PO-{uuid.uuid4().hex[:8].upper()}"
        return SuspendPayload(
            suspend_reason="purchase_order_approval",
            suspend_context={
                "task_id": task_id,
                "decision_type": "purchase_order",
                "store_id": _store_profile().get("store_id"),
                "items": resolved,
                "total_usd": basket_total,
                "currency": DEFAULT_CURRENCY,
                "threshold_usd": threshold,
                "reason": reason or "(no reason provided)",
                "approver_role": "store_manager",
                "human_description": (
                    f"Approve a purchase order ({lines_desc}) totaling "
                    f"${basket_total:.2f}, over the ${threshold:.0f} approval "
                    f"limit? Reason: {reason or '(none)'}."
                ),
                "created_at": _now_iso(),
                "instructions": (
                    "Approve to place the order; reject to cancel it. After an "
                    "approval the agent will call place_purchase_order_approved "
                    "to place the basket — your approval IS the authorization."
                ),
            },
        ).to_json()

    @app.tool(is_local=True)
    def place_purchase_order_approved(
        items: list[dict[str, Any]], reason: str = ""
    ) -> str:
        """Finalize the APPROVED purchase-order basket.

        Call this immediately after a reviewer ``approve`` message. The approved
        basket was stashed when the order was submitted, so you can call this with
        an empty ``items`` list and it will place that basket; passing the same
        ``items`` from the approval request also works.

        SERVER-SIDE GATE: this refuses unless a purchase order was actually
        submitted for approval (i.e. the tool suspended and a reviewer responded).
        You cannot place an order by calling this directly — there is nothing to
        finalize until ``place_purchase_order`` has suspended for approval. Placing
        is idempotent: if this exact basket was already placed in the last couple
        of minutes, the existing PO is returned rather than duplicated (so a
        replayed resume can't double-order).
        """
        user_id = _current_user_id(app)

        # APPROVAL GATE. The submit step (and only the submit step) stashes the
        # basket with submitted_for_approval=True. If there's no such stash, no PO
        # was ever routed for approval — so the model is trying to self-approve.
        # Refuse and write nothing. This is enforced in code, not by the prompt.
        stashed, stashed_reason, submitted = _load_pending_basket(user_id)
        if not submitted:
            return _format_json(
                {
                    "status": "error",
                    "error": (
                        "No purchase order is awaiting approval. A PO must be "
                        "submitted via place_purchase_order (manager_confirmed="
                        "true), which suspends for a reviewer, BEFORE it can be "
                        "placed. Nothing was written."
                    ),
                    "hint": (
                        "If the manager wants to order, call place_purchase_order "
                        "with manager_confirmed=false first and ask them to approve."
                    ),
                }
            )

        # The authoritative basket is the one stashed at submit time — that's
        # exactly what the manager approved. Prefer it over the model's
        # re-supplied `items`, which smaller models often drop or garble on the
        # finalize call. Fall back to `items` only if the stash is somehow empty.
        resolved, unknown = _resolve_basket(stashed)
        if resolved:
            if not reason:
                reason = stashed_reason
        else:
            resolved, unknown = _resolve_basket(items)

        if not resolved:
            return _format_json(
                {
                    "status": "error",
                    "error": "No valid items in the approved basket"
                    + (f" (unknown: {unknown})" if unknown else "")
                    + ".",
                    "hint": "Pass the exact SKUs from the approval request.",
                }
            )
        total = round(sum(line["extended_cost_usd"] for line in resolved), 2)
        result = _place_po_basket(app, resolved, total, reason, requires_approval=True)
        _clear_pending_basket(user_id)
        return result

    # ── EXPIRY / MARKDOWN ─────────────────────────────────────────────────────

    @app.tool(is_local=False)
    def get_expiring_inventory() -> str:
        """List perishable SKUs that are near-expiry or already expired.

        "Near-expiry" means within the store's near-expiry window (see
        `explain_term`); expired items are flagged separately for disposal.
        Each item includes a suggested markdown depth per the markdown SOP.
        """
        window = _near_expiry_days()
        near, expired = [], []
        for sku_doc in mongo.find_docs(mongo.skus_collection()):
            if not sku_doc.get("perishable"):
                continue
            days = _days_until(sku_doc.get("expiry_date"))
            if days is None:
                continue
            entry = {
                "sku": sku_doc.get("sku"),
                "name": sku_doc.get("name"),
                "on_hand": sku_doc.get("on_hand"),
                "expiry_date": sku_doc.get("expiry_date"),
                "days_to_expiry": days,
                **_suggested_markdown(sku_doc),
            }
            if days < 0:
                expired.append(entry)
            elif days <= window:
                near.append(entry)
        return _format_json(
            {
                "near_expiry_window_days": window,
                "near_expiry": near,
                "expired": expired,
            }
        )

    @app.tool(is_local=False)
    def get_dead_skus() -> str:
        """List "dead" SKUs — items with no sale for the store's dead-SKU window
        (see `explain_term`). Candidates for markdown, delist, or shelf-space
        reallocation.
        """
        threshold_days = _dead_sku_days()
        dead = []
        for sku_doc in mongo.find_docs(mongo.skus_collection()):
            last_sold = _days_until(sku_doc.get("last_sold_date"))
            if last_sold is None:
                continue
            days_since = -last_sold  # last_sold_date is in the past
            if days_since >= threshold_days:
                dead.append(
                    {
                        "sku": sku_doc.get("sku"),
                        "name": sku_doc.get("name"),
                        "on_hand": sku_doc.get("on_hand"),
                        "last_sold_date": sku_doc.get("last_sold_date"),
                        "days_since_last_sale": days_since,
                    }
                )
        return _format_json({"dead_sku_window_days": threshold_days, "dead_skus": dead})

    @app.tool(is_local=False)
    def apply_markdown(items: list[dict[str, Any]], reason: str = "") -> str:
        """Apply markdowns and/or disposals for ONE OR MORE items in a single
        basket, and dispatch the work to the floor team. This is the only
        markdown tool — always pass every item you want to act on in one call;
        never call it once per item.

        ``items`` is a list of
        ``{"sku": "<exact SKU>", "action": "markdown"|"disposal", "to_price"?: <float>, "reason"?: "..."}``.
        For a markdown, omit ``to_price`` (or pass 0) to use the SOP-suggested
        price. Unknown SKUs / bad actions reject the whole basket.

        Markdowns and disposals do NOT require manager approval — apply them
        directly. After applying, tell the manager what was done (every line,
        with reference numbers) and that the floor team has been notified to
        action it. Idempotent per line: re-applying the same line won't create a
        duplicate record.
        """
        resolved, problems = _resolve_markdown_basket(items)
        if problems:
            catalog = [
                {"sku": d.get("sku"), "name": d.get("name")}
                for d in mongo.find_docs(mongo.skus_collection())
            ]
            return _format_json(
                {
                    "status": "error",
                    "error": f"Invalid items in the markdown request: {problems}.",
                    "hint": (
                        "Use exact catalog SKUs (from get_expiring_inventory / "
                        "get_dead_skus) and action 'markdown' or 'disposal'. "
                        "Re-call apply_markdown with corrected `items`."
                    ),
                    "valid_skus": catalog,
                }
            )
        if not resolved:
            return _format_json({"status": "error", "error": "No valid items to act on."})
        results = [_apply_markdown_line(app, ln) for ln in resolved]
        placed = [r for r in results if r.get("success")]
        return _format_json(
            {
                "success": True,
                "count": len(placed),
                "team_notified": True,
                "message": "; ".join(r["message"] for r in results),
                "markdowns": results,
            }
        )

    # ── PLANOGRAM ─────────────────────────────────────────────────────────────

    @app.tool(is_local=False)
    def check_planogram_compliance(shelf_id: str = "") -> str:
        """Compare the observed shelf state against the approved planogram and
        return any violations plus a reset task list (per the planogram reset
        SOP). This is driven entirely by shelf DATA — no images.

        Args:
            shelf_id: Optional shelf to check (e.g. "BEV-COOLER-1"). Blank
                checks every shelf with both a planogram and an observed state.
        """
        plano_query = {"shelf_id": shelf_id} if shelf_id else {}
        planograms = mongo.find_docs(mongo.planogram_collection(), plano_query)
        if not planograms:
            return _format_json(
                {"message": f"No planogram found{f' for {shelf_id}' if shelf_id else ''}."}
            )

        shelves = []
        for plano in planograms:
            sid = plano.get("shelf_id")
            observed = mongo.shelf_state_collection().find_one(
                {"shelf_id": sid}, projection={"_id": 0}
            )
            plan_by_pos = {f["position"]: f for f in plano.get("facings", [])}
            obs_by_pos = {f["position"]: f for f in (observed or {}).get("facings", [])}

            violations, reset_tasks = [], []
            for pos, plan in sorted(plan_by_pos.items()):
                obs = obs_by_pos.get(pos)
                want_sku = plan.get("sku")
                want_facings = plan.get("facings")
                if obs is None:
                    violations.append(
                        {"position": pos, "type": "missing", "expected_sku": want_sku}
                    )
                    reset_tasks.append(
                        f"Position {pos}: stock {want_sku} ({want_facings} facings) — currently empty."
                    )
                    continue
                if obs.get("sku") != want_sku:
                    violations.append(
                        {
                            "position": pos,
                            "type": "wrong_sku",
                            "expected_sku": want_sku,
                            "observed_sku": obs.get("sku"),
                        }
                    )
                    reset_tasks.append(
                        f"Position {pos}: remove off-plan {obs.get('sku')} and restore "
                        f"{want_sku} ({want_facings} facings)."
                    )
                elif obs.get("facings") != want_facings:
                    violations.append(
                        {
                            "position": pos,
                            "type": "wrong_facings",
                            "sku": want_sku,
                            "expected_facings": want_facings,
                            "observed_facings": obs.get("facings"),
                        }
                    )
                    reset_tasks.append(
                        f"Position {pos}: adjust {want_sku} from {obs.get('facings')} to "
                        f"{want_facings} facings."
                    )
            shelves.append(
                {
                    "shelf_id": sid,
                    "planogram_version": plano.get("planogram_version"),
                    "observed_at": (observed or {}).get("observed_at"),
                    "compliant": not violations,
                    "violations": violations,
                    "reset_tasks": reset_tasks,
                }
            )
        return _format_json({"shelves": shelves})

    @app.tool(is_local=False)
    def apply_planogram_change(
        shelf_id: str, change: str, reason: str = ""
    ) -> str:
        """Apply a shelf reset / planogram change by dispatching the reset task
        as a work order for the floor staff. Layout changes do NOT require
        manager approval — dispatch directly, then tell the manager the floor
        team has been notified to action the reset.

        Note: this records the reset (per-manager audit record + episode) and
        dispatches it as a work order; it deliberately does NOT rewrite the
        shared, observed ``shelf_state`` collection. The observed state is
        updated by the next physical shelf scan, not by this dispatch — which
        also keeps the planogram scenario repeatable across sessions / users
        without re-seeding.

        Args:
            shelf_id: The shelf to reset (e.g. "BEV-COOLER-1").
            change: A short description of the reset (e.g. "remove gift cards,
                restore energy drinks, refill water to 4 facings").
            reason: Short justification.
        """
        plano = mongo.planogram_collection().find_one(
            {"shelf_id": shelf_id}, projection={"_id": 0}
        )
        if not plano:
            return f"No planogram found for shelf {shelf_id!r}."
        user_id = _current_user_id(app)
        _safe_save_episode(
            app,
            title=f"Planogram reset dispatched: {shelf_id}",
            content=(
                f"Shelf {shelf_id} reset dispatched to floor staff to match "
                f"planogram {plano.get('planogram_version', '')}. "
                f"Change: {change}. Reason: {reason or '(none)'}."
            ),
            tags=["planogram", "reset", shelf_id],
            user_id=user_id,
        )
        return _format_json(
            {
                "success": True,
                "shelf_id": shelf_id,
                "target_planogram_version": plano.get("planogram_version"),
                "status": "reset_dispatched",
                "team_notified": True,
                "message": (
                    f"Shelf {shelf_id} reset has been dispatched to the floor "
                    f"team as a work order. Reset: {change}."
                ),
            }
        )

    # ── MEMORY ────────────────────────────────────────────────────────────────

    @app.tool(is_local=False)
    def recall_store_context(query: str = "") -> str:
        """Recall everything known about this store for the current manager.

        Returns four buckets:
          - ``store_facts``: semantic facts (store profile, equipment notes,
            saved preferences) for this manager.
          - ``recent_decisions``: episodic summaries of past shifts / decisions.
          - ``purchase_orders`` and ``markdowns``: this manager's REAL records
            from MongoDB (newest first) — the authoritative source for "what
            did I order / mark down". Never invent references.

        Call this at the start of a returning session, or whenever the manager
        asks "what should I watch today" / "what did we decide".
        """
        user_id = _current_user_id(app)
        context = ""
        episodes: list[Any] = []
        memory_error: str | None = None
        # Store facts and decision episodes are this manager's private memory;
        # read with visibility="private" to match the write (best practice:
        # reads pass the same visibility/user_id as the write).
        try:
            context = app.memory.build_context(
                query=query or f"store profile, equipment and past decisions for {user_id}",
                user_id=user_id,
                visibility="private",
            )
        except Exception as exc:  # noqa: BLE001
            memory_error = f"semantic: {exc}"
            logger.warning("recall_store_context (semantic) failed: %s", exc)
        try:
            episodes = (
                app.memory.search_episodes(
                    query=query or "store operations reorder markdown planogram",
                    user_id=user_id,
                    visibility="private",
                    top_k=5,
                )
                or []
            )
        except Exception as exc:  # noqa: BLE001
            memory_error = f"{memory_error or ''} episodic: {exc}".strip()

        pos = _purchase_orders_for_user(user_id)
        mds = _markdowns_for_user(user_id)

        if not context and not episodes and not pos and not mds:
            return _format_json(
                {
                    "user_id": user_id,
                    "found": False,
                    "message": "No stored context, decisions, or records yet for this manager.",
                    **({"memory_error": memory_error} if memory_error else {}),
                }
            )
        return _format_json(
            {
                "user_id": user_id,
                "found": True,
                "store_facts": context,
                "recent_decisions": episodes,
                "purchase_orders": pos,
                "markdowns": mds,
                **({"memory_error": memory_error} if memory_error else {}),
            }
        )

    @app.tool(is_local=False)
    def explain_term(term: str) -> str:
        """Look up a convenience-retail domain term (taxonomic memory), e.g.
        "dead SKU", "near-expiry", "reorder point", "planogram". Use this to
        ground your language in the store's shared definitions.
        """
        # Taxonomic terms are seeded org-wide; read with visibility="org" to
        # match the write (best practice: reads pass the same visibility as the
        # corresponding write — see memory-test-agent).
        try:
            exact = app.memory.get_taxonomic_term(
                domain=TAXONOMIC_DOMAIN, term=term, visibility="org"
            )
            if exact:
                return _format_json({"found": True, "term": term, "definition": exact})
        except Exception as exc:  # noqa: BLE001
            logger.warning("explain_term get_taxonomic_term failed: %s", exc)
        try:
            results = app.memory.search_taxonomic(
                query=term, domain=TAXONOMIC_DOMAIN, visibility="org", top_k=3
            )
            if results:
                return _format_json({"found": True, "term": term, "matches": results})
        except Exception as exc:  # noqa: BLE001
            logger.warning("explain_term search_taxonomic failed: %s", exc)
        return _format_json(
            {"found": False, "term": term, "message": "No definition on file for this term."}
        )

    @app.tool(is_local=False)
    def get_sop(procedure: str) -> str:
        """Fetch a standard operating procedure (procedural memory). Known
        procedures: ``reorder-sop``, ``markdown-sop``, ``planogram-reset-sop``.
        Follow the SOP when recommending an action.
        """
        # The memory server requires lowercase-hyphen procedure names; normalize
        # whatever the model passes (e.g. "reorder_sop" / "Reorder SOP") so the
        # lookup matches the stored name.
        name = _slug_procedure(procedure)
        # SOPs are seeded org-wide; read with visibility="org" to match the write.
        try:
            proc = app.memory.get_procedure(procedure_name=name, visibility="org")
            if proc:
                return _format_json({"found": True, "procedure": name, "sop": proc})
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_sop failed: %s", exc)
        return _format_json(
            {
                "found": False,
                "procedure": name,
                "message": (
                    "No SOP found. Known procedures: reorder-sop, markdown-sop, "
                    "planogram-reset-sop."
                ),
            }
        )

    @app.tool(is_local=False)
    def save_report_routine(steps: str, summary: str = "") -> str:
        """Save THIS manager's personalized morning-rundown routine — the extra
        checks they want included in their overnight report every time, beyond
        the base report. This is **procedural memory**: a learned, per-manager
        workflow.

        Save it ONLY after the manager explicitly agrees (e.g. you offered "want
        me to include expiring items and the planogram check in your rundown
        from now on?" and they said yes). Once saved, future sessions
        automatically run these checks during the rundown (the routine is
        auto-injected into context).

        Note: a routine is create-once. If one already exists this returns the
        current routine rather than overwriting it (the memory backend does not
        support in-place updates).

        Args:
            steps: Comma- or newline-separated checks to add to the rundown,
                e.g. "check expiring/near-expiry inventory, check planogram
                compliance for the beverage cooler".
            summary: Optional one-line description of the routine.
        """
        try:
            user_id = app.get_current_user_id()
            routine = _routine_name(user_id)
            existing = app.memory.get_procedure(procedure_name=routine, visibility="org")
            if existing:
                return _format_json(
                    {
                        "status": "already_exists",
                        "routine": routine,
                        "current": existing.get("content")
                        if isinstance(existing, dict)
                        else str(existing),
                        "message": (
                            "You already have a saved rundown routine; keeping it."
                        ),
                    }
                )
            step_list = [s.strip() for s in re.split(r"[,\n]", steps) if s.strip()]
            content = (
                "In addition to the base overnight report (sales, waste, voids, "
                "out-of-stocks, equipment alarms), always include: "
                + "; ".join(step_list)
                + "."
            )
            # Server's ProceduralStep schema requires step_type/content/description.
            proc_steps = [
                {
                    "step_type": "instruction",
                    "content": s,
                    "description": s[:60],
                }
                for s in step_list
            ]
            result = app.memory.save_procedure(
                procedure=routine,
                description=summary or "Manager's personalized morning-rundown routine",
                content=content,
                user_id=user_id,
                steps=proc_steps,
                visibility="org",
            )
            if not result:
                return _format_json({"status": "error", "error": "Could not save routine"})
            return _format_json(
                {
                    "status": "saved",
                    "routine": routine,
                    "added_checks": step_list,
                    "message": (
                        "Saved. From now on your overnight report will also "
                        f"include: {', '.join(step_list)}."
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("save_report_routine failed: %s", exc)
            return _format_json({"status": "error", "error": str(exc)})

    @app.tool(is_local=False)
    def get_report_routine() -> str:
        """Fetch THIS manager's saved morning-rundown routine (procedural
        memory), if any. The routine is also auto-injected into your context
        each turn; call this to confirm what's saved.
        """
        try:
            user_id = app.get_current_user_id()
            routine = app.memory.get_procedure(
                procedure_name=_routine_name(user_id), visibility="org"
            )
            if routine:
                return _format_json({"found": True, "routine": routine})
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_report_routine failed: %s", exc)
        return _format_json(
            {
                "found": False,
                "message": (
                    "No saved rundown routine for this manager yet. After "
                    "answering rundown follow-ups, offer to save one."
                ),
            }
        )

    @app.tool(is_local=False)
    def remember_store_fact(label: str, fact: str, tags: str = "") -> str:
        """Save a durable fact about this store or the manager's preferences.

        Use when the manager shares something worth remembering across shifts:
        a recurring demand pattern, an equipment quirk, a standing preference.

        Args:
            label: Short slug, e.g. "freezer3", "weekend_demand". One label per
                fact — calling again with the same label overwrites it.
            fact: Free-form sentence capturing the fact.
            tags: Optional comma-separated tags.
        """
        try:
            user_id = app.get_current_user_id()
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            ok = app.memory.save_semantic(
                text=f"{label}: {fact}",
                label=f"{user_id}_{label}",
                source="store_manager_agent",
                metadata={"type": "store_fact", "label": label, "tags": tag_list},
                user_id=user_id,
                visibility="private",
            )
            if not ok:
                return _format_json({"status": "error", "error": "Memory not enabled"})
            return _format_json({"status": "saved", "label": label, "user_id": user_id})
        except Exception as exc:  # noqa: BLE001
            logger.warning("remember_store_fact failed: %s", exc)
            return _format_json({"status": "error", "error": str(exc)})

    @app.tool(is_local=False)
    def save_shift_summary(title: str, summary: str, tags: str = "") -> str:
        """Save an episodic summary of the current shift / conversation.

        Call once at a natural endpoint (end of the rundown, after a set of
        decisions) so the next shift has narrative context. Keep ``summary`` to
        2-4 sentences with concrete decisions and any open follow-ups.
        """
        try:
            user_id = app.get_current_user_id()
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            episode_id = app.memory.save_episode(
                title=title,
                content=summary,
                summary=summary,
                participants=EPISODE_PARTICIPANTS,
                tags=tag_list,
                user_id=user_id,
                visibility="private",
            )
            if not episode_id:
                return _format_json({"status": "error", "error": "Memory not enabled"})
            return _format_json(
                {"status": "saved", "episode_id": episode_id, "user_id": user_id}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("save_shift_summary failed: %s", exc)
            return _format_json({"status": "error", "error": str(exc)})

    @app.tool(is_local=True)
    def seed_store_memory() -> str:
        """Seed the four memory types for the demo (idempotent).

        Writes:
          - **semantic** store facts (profile, equipment) under THIS user, so
            they surface in the manager's own context;
          - **taxonomic** domain definitions (org-scoped, shared);
          - **procedural** SOPs (org-scoped, shared);
          - one **episodic** lesson (a past heatwave stockout) under THIS user.

        Call this once when asked to "seed store memory" / "initialize memory".
        """
        from store_manager_agent.seed import seed_memory

        try:
            return _format_json(seed_memory(app))
        except Exception as exc:  # noqa: BLE001
            logger.warning("seed_store_memory failed: %s", exc)
            return _format_json({"status": "error", "error": str(exc)})


def _apply_markdown_line(app: Any, line: dict[str, Any]) -> dict[str, Any]:
    """Insert one markdown/disposal record (idempotent per SKU+action)."""
    user_id = _current_user_id(app)
    sku = line["sku"]
    name = line.get("name", sku)
    action = line["action"]
    to_price = float(line.get("to_price", 0) or 0)
    pct = line.get("pct", 100 if action == "disposal" else 0)
    retail = float(line.get("from_price", 0) or 0)
    verb = (
        f"marked down to ${to_price:.2f} ({pct}% off)"
        if action == "markdown"
        else "removed from sale for disposal"
    )

    dupe = _recent_duplicate(
        mongo.markdowns_collection(),
        {"user_id": user_id, "sku": sku, "action": action, "status": "PLACED"},
    )
    if dupe:
        return {
            "success": True,
            "idempotent": True,
            "markdown_ref": dupe.get("markdown_ref"),
            "message": f"{name} was already {verb} ({dupe.get('markdown_ref')})",
        }

    ref = _generate_ref(mongo.markdowns_collection(), "markdown_ref", "MD")
    doc = {
        "markdown_ref": ref,
        "user_id": user_id,
        "store_id": _store_profile().get("store_id"),
        "sku": sku,
        "name": name,
        "action": action,
        "from_price": retail,
        "to_price": to_price,
        "pct": pct,
        "status": "PLACED",
        "reason": line.get("reason") or "(no reason provided)",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    mongo.markdowns_collection().insert_one(dict(doc))
    doc.pop("_id", None)
    _safe_save_episode(
        app,
        title=f"{action.title()} applied: {name}",
        content=f"{action.title()} {ref} on {name} ({sku}) — {verb}. Reason: {line.get('reason') or '(none)'}.",
        tags=["markdown", action, sku],
        user_id=user_id,
    )
    return {
        "success": True,
        "markdown_ref": ref,
        "message": f"{name} {verb} ({ref})",
        "markdown": doc,
    }


def _place_po_basket(
    app: Any,
    lines: list[dict[str, Any]],
    total: float,
    reason: str,
    *,
    requires_approval: bool,
) -> str:
    """Insert a single multi-line purchase order, stamp user_id, save an episode.

    Idempotent: a re-delivered approval (or a replayed tool batch) for the same
    basket returns the existing PO instead of inserting a duplicate.
    """
    user_id = _current_user_id(app)
    sku_signature = sorted(f"{ln['sku']}:{ln['qty_cases']}" for ln in lines)
    lines_desc = ", ".join(f"{ln['qty_cases']}x {ln['name']}" for ln in lines)

    # Idempotency: match a recent PLACED PO with the identical line-set.
    for cand in mongo.purchase_orders_collection().find(
        {"user_id": user_id, "status": "PLACED"}, projection={"_id": 0}
    ).sort("created_at", -1).limit(10):
        cand_sig = sorted(
            f"{ln.get('sku')}:{ln.get('qty_cases')}" for ln in cand.get("lines", [])
        )
        if cand_sig == sku_signature and _recent_duplicate(
            mongo.purchase_orders_collection(), {"po_ref": cand.get("po_ref")}
        ):
            return _format_json(
                {
                    "success": True,
                    "idempotent": True,
                    "po_ref": cand.get("po_ref"),
                    "message": (
                        f"This order ({lines_desc}) was already placed "
                        f"(reference {cand.get('po_ref')}). No duplicate created."
                    ),
                    "purchase_order": cand,
                }
            )

    ref = _generate_ref(mongo.purchase_orders_collection(), "po_ref", "PO")
    doc = {
        "po_ref": ref,
        "user_id": user_id,
        "store_id": _store_profile().get("store_id"),
        "lines": lines,
        "total_usd": total,
        "currency": DEFAULT_CURRENCY,
        "status": "PLACED",
        "reason": reason or "(no reason provided)",
        "requires_approval": requires_approval,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    mongo.purchase_orders_collection().insert_one(dict(doc))
    doc.pop("_id", None)
    _safe_save_episode(
        app,
        title=f"PO placed: {lines_desc}",
        content=(
            f"Purchase order {ref} placed — {lines_desc}. Total ${total:.2f}. "
            f"Reason: {reason or '(none)'}. "
            f"{'Manager-approved (over threshold).' if requires_approval else 'Auto-placed (under threshold).'}"
        ),
        tags=["purchase_order", "reorder"] + [ln["sku"] for ln in lines],
        user_id=user_id,
    )
    return _format_json(
        {
            "success": True,
            "po_ref": ref,
            "message": f"Purchase order {ref} placed ({lines_desc}) — ${total:.2f}.",
            "purchase_order": doc,
        }
    )
