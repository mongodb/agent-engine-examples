#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# reset_demo.sh — Wipe learned data & sessions for a fresh recruiting-assistant
# demo while preserving all pre-seeded memories (candidate profiles, org
# insights, taxonomic entries).
#
# What gets deleted:
#   1. Learned recruiter insights    (semantic, source=recruiter_feedback)
#   2. Episodic memories             (conversation summaries for the org)
#   3. LangGraph checkpoints         (session thread state)
#   4. Observability logs            (traces.jsonl, executions.jsonl)
#
# What is preserved:
#   - Candidate profiles             (semantic, source=candidate_profile)
#   - Org-wide hiring insights       (semantic, source=funnel_analytics)
#   - All taxonomic memories         (role signals, role families, outreach)
#
# Usage:
#   cd agents/recruiting-assistant-agent && ./reset_demo.sh
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load env for MongoDB URI
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

MONGODB_URI="${MONGODB_URI:?MONGODB_URI not set — create .env or export it}"
MEMORY_DB="${MEMORY_DATABASE:-memory}"
CHECKPOINT_DB="${CHECKPOINT_DB_NAME:-agent-checkpoints}"
ORG_ID="${ORG_ID:-org_recruiting_demo}"

echo "=============================================="
echo "  Recruiting Assistant — Demo Reset"
echo "=============================================="
echo "Memory DB:      $MEMORY_DB"
echo "Checkpoint DB:  $CHECKPOINT_DB"
echo "Org ID:         $ORG_ID"
echo ""

PYTHON="${SCRIPT_DIR}/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
fi

$PYTHON - "$MONGODB_URI" "$MEMORY_DB" "$CHECKPOINT_DB" "$ORG_ID" <<'PYEOF'
import sys
from pymongo import MongoClient

uri, mem_db, ckpt_db, org_id = sys.argv[1:5]
client = MongoClient(uri)

# ── 1. Delete learned recruiter insights (semantic) ──────────────────────
coll = client[mem_db]["memory_semantic"]
r = coll.delete_many({"org_id": org_id, "source": "recruiter_feedback"})
print(f"[1/5] Deleted {r.deleted_count} learned recruiter insights (semantic, source=recruiter_feedback)")

# ── 2. Delete episodic memories ──────────────────────────────────────────
coll = client[mem_db]["memory_episodic"]
r = coll.delete_many({"org_id": org_id})
print(f"[2/5] Deleted {r.deleted_count} episodic memories (org={org_id})")

# ── 3. Drop LangGraph checkpoint collections ─────────────────────────────
ckpt = client[ckpt_db]
dropped = []
for name in ckpt.list_collection_names():
    ckpt.drop_collection(name)
    dropped.append(name)
if dropped:
    print(f"[3/5] Dropped {len(dropped)} checkpoint collection(s): {', '.join(dropped)}")
else:
    print(f"[3/5] No checkpoint collections to drop")

client.close()
PYEOF

# ── 4. Clear observability logs ──────────────────────────────────────────
OBS_DIR="$SCRIPT_DIR/observability"
truncated=0
for f in "$OBS_DIR"/traces.jsonl "$OBS_DIR"/executions.jsonl; do
    if [ -f "$f" ]; then
        : > "$f"
        truncated=$((truncated + 1))
    fi
done
echo "[4/4] Cleared $truncated observability log file(s)"

echo ""
echo "✓ Demo reset complete. Pre-seeded memories are intact."
echo "  Restart the agent server to pick up a clean state."
