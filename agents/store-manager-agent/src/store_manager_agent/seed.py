"""Seed the local MongoDB cluster with sample store-operations data, and
(through a running agent) the four memory types.

Two halves:

1. **Mongo domain data** (`main()` / `seed_data()`): store profile, SKUs, the
   overnight operational report, a forecast event, and the planogram + observed
   shelf state. This runs offline (no LLM, no embeddings) and is fully
   idempotent — every run drops and reinserts the catalog/report collections and
   clears the demo user's purchase orders / markdowns, so re-running returns the
   store to a clean demo state. Date-sensitive fields are stored as INTEGER DAY
   OFFSETS (e.g. heatwave ``date_offset_days: 2``, sandwich ``expiry_offset_days:
   2``) and resolved to absolute dates at READ time by ``mongo.resolve_dates()``
   — so the demo is EVERGREEN: it stays valid however long after seeding it runs,
   with no need to re-seed for dates. (Re-seed is still useful to clear demo
   purchase orders / markdowns between runs.)

2. **Memory** (`seed_memory(app)`): the four memory types. This needs the
   running agent runtime (`app.memory`), so it is invoked through the
   `seed_store_memory` tool rather than this CLI. Semantic + episodic memory are
   written under the CURRENT user_id (so they surface in that manager's context);
   taxonomic + procedural are org-scoped (shared across managers).

Run the Mongo seed with:
    uv run store-manager-seed
    uv run store-manager-seed --drop   # full teardown, then exit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv

from store_manager_agent import mongo

logger = logging.getLogger(__name__)

STORE_ID = "2711"
TAXONOMIC_DOMAIN = "convenience_retail"
SEED_EPISODE_TAG = "__seed__"  # marks the seeded lesson so re-seeding can dedupe
DEMO_USER_ID = os.environ.get("DEMO_USER_ID", "")  # optional: which user's POs/MDs to clear


def _today() -> Any:
    return datetime.now(timezone.utc).date()


def _iso_date(days_from_today: int) -> str:
    return (_today() + timedelta(days=days_from_today)).isoformat()


# Date-sensitive demo fields are seeded as INTEGER DAY OFFSETS (relative to
# "now") and resolved to absolute dates at READ time by mongo.resolve_dates().
# This keeps the demo evergreen — a heatwave seeded "+2 days" is always 2 days
# out, no matter how long after seeding the demo runs, with NO re-seeding.


# ─── Store profile ─────────────────────────────────────────────────────────────


def _store_profile() -> dict[str, Any]:
    return {
        "store_id": STORE_ID,
        "name": f"Store #{STORE_ID} — Campus",
        "format": "campus_convenience",
        "address": "120 University Ave",
        "timezone": "America/New_York",
        "traits": [
            "high foot traffic 11:00-14:00 and 22:00-01:00",
            "heavy slush and energy-drink demand",
            "freezer #3 unreliable — intermittent high-temp alarms",
        ],
        "po_approval_threshold_usd": 250,
        "near_expiry_days": 3,
        "dead_sku_days": 21,
    }


# ─── SKUs (dates relative to today so the demo never goes stale) ────────────────


def _skus() -> list[dict[str, Any]]:
    return [
        {
            "sku": "WATER-500ML-24PK",
            "name": "Spring Water 500ml (24-pack case)",
            "category": "beverage_water",
            "unit_cost": 8.00,
            "retail_price": 0,
            "currency": "USD",
            "on_hand": 3,
            "par_level": 40,
            "reorder_point": 8,
            "case_pack": 24,
            "lead_time_days": 1,
            "expiry_offset_days": None,
            "perishable": False,
            "last_sold_offset_days": 0,
            "shelf_id": "BEV-COOLER-1",
            "weekly_velocity": 40,
        },
        {
            "sku": "ENERGY-DRINK-12PK",
            "name": "Energy Drink 250ml (12-pack)",
            "category": "beverage_energy",
            "unit_cost": 14.40,
            "retail_price": 0,
            "currency": "USD",
            "on_hand": 5,
            "par_level": 12,
            "reorder_point": 8,
            "case_pack": 12,
            "lead_time_days": 2,
            "expiry_offset_days": None,
            "perishable": False,
            "last_sold_offset_days": 0,
            "shelf_id": "BEV-COOLER-1",
            "weekly_velocity": 22,
        },
        {
            "sku": "SLUSH-COLA-SYRUP-5L",
            "name": "Slush Cola Syrup 5L (bag-in-box)",
            "category": "frozen_beverage_syrup",
            "unit_cost": 12.00,
            "retail_price": 0,
            "currency": "USD",
            "on_hand": 2,
            "par_level": 8,
            "reorder_point": 4,
            "case_pack": 1,
            "lead_time_days": 1,
            "expiry_offset_days": None,
            "perishable": False,
            "last_sold_offset_days": 0,
            "shelf_id": "BEV-FOUNTAIN",
            "weekly_velocity": 6,
        },
        {
            "sku": "SANDWICH-TURKEY-CLUB",
            "name": "Turkey Club Sandwich",
            "category": "fresh_food",
            "unit_cost": 2.10,
            "retail_price": 5.49,
            "currency": "USD",
            "on_hand": 9,
            "par_level": 12,
            "reorder_point": 4,
            "case_pack": 6,
            "lead_time_days": 1,
            "expiry_offset_days": 2,  # near-expiry → markdown (applied directly)
            "perishable": True,
            "last_sold_offset_days": 0,
            "shelf_id": "GRAB-N-GO-1",
            "weekly_velocity": 35,
        },
        {
            "sku": "SANDWICH-EGG-SALAD",
            "name": "Egg Salad Sandwich",
            "category": "fresh_food",
            "unit_cost": 1.90,
            "retail_price": 4.99,
            "currency": "USD",
            "on_hand": 4,
            "par_level": 10,
            "reorder_point": 3,
            "case_pack": 6,
            "lead_time_days": 1,
            "expiry_offset_days": -1,  # expired → disposal (applied directly)
            "perishable": True,
            "last_sold_offset_days": -1,
            "shelf_id": "GRAB-N-GO-1",
            "weekly_velocity": 18,
        },
        {
            "sku": "GIFTCARD-NOVELTY-BLANK",
            "name": "Novelty Gift Card (blank)",
            "category": "general_merch",
            "unit_cost": 1.20,
            "retail_price": 4.99,
            "currency": "USD",
            "on_hand": 22,
            "par_level": 12,
            "reorder_point": 4,
            "case_pack": 12,
            "lead_time_days": 5,
            "expiry_offset_days": None,
            "perishable": False,
            "last_sold_offset_days": -40,  # 40d > 21 → dead SKU
            "shelf_id": "BEV-COOLER-1",
            "weekly_velocity": 0,
        },
        {
            "sku": "TAQUITO-BEEF-FROZEN",
            "name": "Beef Taquito (frozen, each)",
            "category": "frozen_food",
            "unit_cost": 0.60,
            "retail_price": 1.99,
            "currency": "USD",
            "on_hand": 40,
            "par_level": 60,
            "reorder_point": 20,
            "case_pack": 50,
            "lead_time_days": 3,
            "expiry_offset_days": 120,
            "perishable": True,
            "last_sold_offset_days": 0,
            "shelf_id": "FREEZER-3",  # ties to the freezer #3 equipment alarm
            "weekly_velocity": 28,
        },
        {
            "sku": "COFFEE-CUPS-16OZ",
            "name": "Coffee Cups 16oz (sleeve)",
            "category": "supplies",
            "unit_cost": 3.20,
            "retail_price": 0,
            "currency": "USD",
            "on_hand": 30,
            "par_level": 24,
            "reorder_point": 10,
            "case_pack": 50,
            "lead_time_days": 2,
            "expiry_offset_days": None,
            "perishable": False,
            "last_sold_offset_days": 0,
            "shelf_id": "COFFEE-BAR",
            "weekly_velocity": 14,
        },
    ]


# ─── Overnight operational report ───────────────────────────────────────────────


def _ops_report() -> dict[str, Any]:
    return {
        "store_id": STORE_ID,
        "report_offset_days": 0,  # resolved to today's date at read time
        "shift": "overnight",
        "sales_total_usd": 4182.55,
        "transactions": 612,
        "waste": [
            {"sku": "SANDWICH-EGG-SALAD", "units": 4, "reason": "expired", "cost_usd": 7.60},
        ],
        "voids": [
            {"register": 2, "count": 7, "amount_usd": 58.20, "note": "manager override spike"},
        ],
        "out_of_stocks": [
            {"sku": "WATER-500ML-24PK", "minutes": 190},
            {"sku": "ENERGY-DRINK-12PK", "minutes": 70},
        ],
        "equipment_alarms": [
            {"asset": "freezer #3", "alarm": "temp_high", "events": 3},
        ],
    }


# ─── Forecast event (heatwave in 2 days) ────────────────────────────────────────


def _weather_forecast() -> list[dict[str, Any]]:
    """A short local forecast. A heatwave lands in 2 days (same day as the
    demand event), so the copilot can cite the weather when prioritizing the
    out-of-stock cold/hydration SKUs. Deterministic — not a live lookup."""
    return [
        {
            "store_id": STORE_ID,
            "location": "campus district",
            "days": [
                {"offset_days": 0, "high_f": 82, "low_f": 64,
                 "conditions": "Partly cloudy", "heat_advisory": False},
                {"offset_days": 1, "high_f": 90, "low_f": 70,
                 "conditions": "Sunny, warming up", "heat_advisory": False},
                {"offset_days": 2, "high_f": 99, "low_f": 78,
                 "conditions": "Heatwave — excessive heat warning", "heat_advisory": True},
                {"offset_days": 3, "high_f": 97, "low_f": 77,
                 "conditions": "Heatwave continues", "heat_advisory": True},
            ],
            "summary": (
                "A heatwave arrives in 2 days with highs near 99°F and an "
                "excessive-heat warning. Expect a sharp spike in demand for "
                "bottled water, energy drinks, and frozen/slush beverages."
            ),
        },
    ]


def _forecast_events() -> list[dict[str, Any]]:
    return [
        {
            "store_id": STORE_ID,
            "event": "heatwave",
            "date_offset_days": 2,  # resolved to today+2 at read time
            "expected_high_f": 99,
            "demand_multiplier": {
                "WATER-500ML-24PK": 2.4,
                "SLUSH-COLA-SYRUP-5L": 2.0,
                "ENERGY-DRINK-12PK": 1.6,
            },
        },
    ]


# ─── Planogram (intended) + observed shelf state (with deviations) ──────────────


def _planogram() -> list[dict[str, Any]]:
    return [
        {
            "store_id": STORE_ID,
            "shelf_id": "BEV-COOLER-1",
            "planogram_version": "current",
            "facings": [
                {"position": 1, "sku": "WATER-500ML-24PK", "facings": 4},
                {"position": 2, "sku": "ENERGY-DRINK-12PK", "facings": 3},
                {"position": 3, "sku": "COFFEE-CUPS-16OZ", "facings": 2},
            ],
        },
    ]


def _shelf_state() -> list[dict[str, Any]]:
    return [
        {
            "store_id": STORE_ID,
            "shelf_id": "BEV-COOLER-1",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "facings": [
                # position 1 under-faced (4 → 2)
                {"position": 1, "sku": "WATER-500ML-24PK", "facings": 2},
                # position 2 wrong SKU (energy drinks replaced by the dead-SKU gift cards)
                {"position": 2, "sku": "GIFTCARD-NOVELTY-BLANK", "facings": 3},
                {"position": 3, "sku": "COFFEE-CUPS-16OZ", "facings": 2},
            ],
        },
    ]


# ─── Mongo seeding (idempotent) ─────────────────────────────────────────────────


def seed_data() -> dict[str, int]:
    """Drop + reinsert domain collections; clear demo user's POs / markdowns.

    Returns a count summary. Fully idempotent: safe to run repeatedly.
    """
    counts: dict[str, int] = {}

    profile = mongo.store_profile_collection()
    profile.delete_many({})
    profile.insert_one(_store_profile())
    counts["store_profile"] = 1

    skus = mongo.skus_collection()
    skus.delete_many({})
    sku_docs = _skus()
    skus.insert_many(sku_docs)
    counts["skus"] = len(sku_docs)

    reports = mongo.ops_reports_collection()
    reports.delete_many({})
    reports.insert_one(_ops_report())
    counts["ops_reports"] = 1

    forecasts = mongo.forecast_events_collection()
    forecasts.delete_many({})
    fc = _forecast_events()
    forecasts.insert_many(fc)
    counts["forecast_events"] = len(fc)

    plano = mongo.planogram_collection()
    plano.delete_many({})
    pg = _planogram()
    plano.insert_many(pg)
    counts["planogram"] = len(pg)

    shelf = mongo.shelf_state_collection()
    shelf.delete_many({})
    ss = _shelf_state()
    shelf.insert_many(ss)
    counts["shelf_state"] = len(ss)

    weather = mongo.weather_forecast_collection()
    weather.delete_many({})
    wf = _weather_forecast()
    weather.insert_many(wf)
    counts["weather_forecast"] = len(wf)

    # Clear runtime-mutated decision records. By default wipe ALL of them so a
    # re-seed is a clean slate; if DEMO_USER_ID is set, only that user's are
    # cleared (useful when several demos share a cluster).
    po_filter = {"user_id": DEMO_USER_ID} if DEMO_USER_ID else {}
    md_filter = {"user_id": DEMO_USER_ID} if DEMO_USER_ID else {}
    counts["purchase_orders_cleared"] = mongo.purchase_orders_collection().delete_many(po_filter).deleted_count
    counts["markdowns_cleared"] = mongo.markdowns_collection().delete_many(md_filter).deleted_count
    # Stashed over-limit baskets are transient; clear them on reseed too.
    mongo.pending_purchase_orders_collection().delete_many(po_filter)

    return counts


def drop_all() -> None:
    """Full teardown: drop every domain collection in store_db.

    Use this when retiring a deployment or switching to a different customer
    demo and you want a truly empty database. Seeded memory (semantic /
    episodic / taxonomic / procedural) lives in the separate memory store and
    is not touched here — switch user_id for a fresh per-manager memory slate,
    or clear the memory database directly for a hard reset.
    """
    db = mongo.store_db()
    for name in mongo.all_data_collections():
        db.drop_collection(name)
        logger.info("Dropped collection %s", name)


# ─── Memory seeding (needs the running runtime; invoked via seed_store_memory) ──


_TAXONOMY: list[dict[str, Any]] = [
    {
        "term": "dead SKU",
        "definition": (
            "An item with zero sales for 21 or more consecutive days; a "
            "candidate for delisting and shelf-space reallocation."
        ),
        "related_terms": ["slow mover", "delist", "planogram reset"],
    },
    {
        "term": "near-expiry",
        "definition": (
            "A perishable item within 3 days of its expiration date; eligible "
            "for markdown to drive sell-through before it becomes waste."
        ),
        "related_terms": ["markdown", "waste", "shrink"],
    },
    {
        "term": "reorder point",
        "definition": (
            "The on-hand quantity at which a replenishment order should be "
            "triggered, accounting for lead time and demand."
        ),
        "related_terms": ["par level", "purchase order", "lead time"],
    },
    {
        "term": "planogram",
        "definition": (
            "The approved shelf layout specifying which SKUs occupy which "
            "positions and how many facings each gets."
        ),
        "related_terms": ["facing", "shelf compliance", "reset"],
    },
]


_PROCEDURES: list[dict[str, Any]] = [
    {
        "procedure": "reorder-sop",
        "description": "How to size and place a replenishment order.",
        "content": (
            "1. Confirm on-hand is at or below the reorder point. "
            "2. Size the order up to par level, adjusted by any upcoming "
            "forecast demand multiplier (e.g. a heatwave). "
            "3. If the order total is over the store's approval limit, route it "
            "for manager approval; otherwise place it directly. "
            "4. Record the outcome."
        ),
        "steps": [
            {"step_type": "instruction", "description": "Check stock level",
             "content": "Confirm on_hand <= reorder_point."},
            {"step_type": "instruction", "description": "Size the order",
             "content": "Size to par level x forecast multiplier, minus on_hand."},
            {"step_type": "instruction", "description": "Route for approval if needed",
             "content": "Over the approval limit -> manager approval; else place directly."},
            {"step_type": "instruction", "description": "Record the outcome",
             "content": "Record the purchase order outcome."},
        ],
    },
    {
        "procedure": "markdown-sop",
        "description": "How to handle near-expiry, expired, and dead inventory.",
        "content": (
            "For near-expiry perishables, apply a tiered markdown: 50% off at 3 "
            "days to expiry, 75% off at 1 day. For expired items, apply a "
            "disposal. For dead SKUs, apply a markdown or delist. Markdowns and "
            "disposals do NOT require manager approval — apply them directly and "
            "notify the floor team to action the change. Record every decision."
        ),
        "steps": [
            {"step_type": "instruction", "description": "Near-expiry markdown tiers",
             "content": "Near-expiry: 50% off at 3 days, 75% off at 1 day."},
            {"step_type": "instruction", "description": "Handle expired stock",
             "content": "Expired: apply a disposal."},
            {"step_type": "instruction", "description": "Handle dead SKUs",
             "content": "Dead SKU: apply a markdown or delist."},
            {"step_type": "instruction", "description": "Apply and notify",
             "content": "Apply markdowns/disposals directly; notify the floor team."},
        ],
    },
    {
        "procedure": "planogram-reset-sop",
        "description": "How to bring a shelf back into planogram compliance.",
        "content": (
            "Diff the observed shelf state against the active planogram. For "
            "each deviation, produce a reset task: restock under-faced positions "
            "to the target facings, and remove any off-plan SKUs in favor of the "
            "planogram SKU. A layout change does NOT require manager approval — "
            "dispatch the reset directly to floor staff as a work order and "
            "notify the team."
        ),
        "steps": [
            {"step_type": "instruction", "description": "Diff shelf vs planogram",
             "content": "Diff observed shelf_state vs active planogram."},
            {"step_type": "instruction", "description": "Restock under-faced positions",
             "content": "Restock under-faced positions to target facings."},
            {"step_type": "instruction", "description": "Remove off-plan SKUs",
             "content": "Remove off-plan SKUs; restore the planogram SKU."},
            {"step_type": "instruction", "description": "Dispatch and notify",
             "content": "Dispatch the reset to floor staff; notify the team."},
        ],
    },
]


def seed_memory(app: Any) -> dict[str, Any]:
    """Seed all four memory types through a running agent runtime.

    Semantic + episodic are written under the CURRENT user_id so they surface in
    that manager's `build_context` / `search_episodes` (those retrieval paths
    filter by user_id). Taxonomic + procedural are org-scoped and shared.
    """
    memory = getattr(app, "memory", None)
    if memory is None:
        return {"status": "error", "error": "Memory is not enabled on this app."}

    try:
        user_id = app.get_current_user_id()
    except Exception:  # noqa: BLE001
        user_id = None
    if not user_id:
        return {"status": "error", "error": "No current user_id; cannot seed per-user memory."}

    summary: dict[str, Any] = {"status": "ok", "user_id": user_id}

    # 1) Semantic store facts (under THIS manager so they surface in context).
    semantic_facts = [
        (
            "store_2711_profile",
            f"Store #{STORE_ID} is a campus convenience store with heavy slush "
            "and energy-drink demand and very high lunch (11:00-14:00) and "
            "late-night (22:00-01:00) traffic.",
        ),
        (
            "store_2711_freezer3",
            f"Freezer #3 at Store #{STORE_ID} is unreliable and throws "
            "intermittent high-temperature alarms; double-check frozen SKUs "
            "stored there (e.g. the beef taquitos).",
        ),
    ]
    sem_ok = 0
    for label, text in semantic_facts:
        try:
            if memory.save_semantic(
                text=text,
                label=f"{user_id}_{label}",
                source="store_manager_agent",
                metadata={"type": "store_fact", "store_id": STORE_ID},
                user_id=user_id,
                visibility="private",
            ):
                sem_ok += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("seed semantic %s failed: %s", label, exc)
    summary["semantic_facts"] = sem_ok

    # 2) Taxonomic definitions (org-scoped, shared).
    tax_ok = 0
    for entry in _TAXONOMY:
        try:
            if memory.save_taxonomic(
                domain=TAXONOMIC_DOMAIN,
                term=entry["term"],
                definition=entry["definition"],
                related_terms=entry.get("related_terms"),
                visibility="org",
            ):
                tax_ok += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("seed taxonomic %s failed: %s", entry["term"], exc)
    summary["taxonomic_terms"] = tax_ok

    # 3) Procedural SOPs (org-scoped, shared). Idempotent: the memory server's
    # update/PATCH route 500s, so we never update-in-place — we create only when
    # the procedure is absent. An already-present SOP counts as a success so a
    # re-seed reports the true state rather than 0. ``steps`` MUST use the
    # server's ProceduralStep schema ({step_type, content, description}); a
    # {step, action} shape 500s the whole save.
    proc_ok = 0
    for proc in _PROCEDURES:
        try:
            if memory.get_procedure(procedure_name=proc["procedure"], visibility="org"):
                proc_ok += 1  # already seeded
                continue
            result = memory.save_procedure(
                procedure=proc["procedure"],
                description=proc["description"],
                content=proc["content"],
                user_id=user_id,
                steps=proc.get("steps"),
                visibility="org",
            )
            if result:
                proc_ok += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("seed procedure %s failed: %s", proc["procedure"], exc)
    summary["procedures"] = proc_ok

    # 4) Episodic lesson (under THIS manager). Dedupe prior seeded copies first.
    ep_ok = 0
    try:
        existing = memory.search_episodes(
            query="heatwave water stockout lesson",
            user_id=user_id,
            visibility="private",
            top_k=10,
        ) or []
        already = any(
            SEED_EPISODE_TAG in (e.get("tags") or [])
            for e in existing
            if isinstance(e, dict)
        )
        if not already:
            ep_id = memory.save_episode(
                title="Heatwave under-order — water sold out",
                content=(
                    "During the last heatwave we under-ordered bottled water and "
                    "WATER-500ML-24PK sold out for about 4 hours mid-afternoon, "
                    "costing an estimated $300 in lost sales. Lesson: pre-order "
                    "water aggressively ahead of any forecast heat."
                ),
                summary=(
                    "Last heatwave: under-ordered water, sold out ~4h, ~$300 lost. "
                    "Pre-order water aggressively ahead of forecast heat."
                ),
                participants=["Store Manager", "Store Copilot"],
                tags=[SEED_EPISODE_TAG, "heatwave", "stockout", "water", "lesson"],
                user_id=user_id,
                visibility="private",
            )
            if ep_id:
                ep_ok = 1
        else:
            summary["episode_note"] = "seed lesson already present; skipped"
    except Exception as exc:  # noqa: BLE001
        logger.warning("seed episode failed: %s", exc)
    summary["episodes"] = ep_ok

    summary["message"] = (
        f"Seeded memory for {user_id}: {sem_ok} semantic facts, {tax_ok} "
        f"taxonomic terms, {proc_ok} procedures, {ep_ok} episode(s). Taxonomic "
        "and procedural memory are org-wide; semantic and episodic are scoped to "
        "this manager."
    )
    return summary


# ─── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
    load_dotenv()

    parser = argparse.ArgumentParser(description="Seed / reset the store-manager-agent data.")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop every domain collection in store_db, then exit without re-seeding.",
    )
    args = parser.parse_args()

    client = mongo.get_client()
    client.admin.command("ping")
    logger.info(
        "Connected to MongoDB at %s (db=%s)",
        os.environ.get("STORE_MONGODB_URI") or os.environ.get("MONGODB_URI", "(default)"),
        os.environ.get("STORE_DATABASE", "store_db"),
    )

    if args.drop:
        drop_all()
        logger.info(
            "Teardown complete: all store_db domain collections dropped. "
            "(Memory is per-user — switch user_id for a fresh memory slate.)"
        )
        return 0

    counts = seed_data()
    logger.info("Seed complete: %s", counts)
    logger.info(
        "Memory is seeded separately through the running agent — send "
        "'seed store memory' in the playground, or run "
        "`uv run python demo.py --only-section seed`."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
