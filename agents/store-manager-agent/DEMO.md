# Store Manager Copilot — Live Demo Script

A 10–12 minute walkthrough you can run live in front of an audience using the
playground UI. It tells one story: **a copilot that helps a store manager run
the store each day — recommending actions, remembering the store, and pausing
for a human on the decisions that spend money.**

The two things this demo is built to land:

1. **Different types of memory.** The copilot uses four — and they behave
   differently on purpose:
   - **Semantic** — facts about *this* store (campus format, demand pattern,
     "freezer #3 is flaky").
   - **Episodic** — what happened before and what was decided ("last heatwave we
     under-ordered water and sold out").
   - **Taxonomic** — shared definitions ("dead SKU", "near-expiry").
   - **Procedural** — shared standard operating procedures (the reorder SOP).
2. **Human-in-the-loop approval where it matters.** Spending money over the
   store's limit (a purchase order) pauses for the manager — you'll show both an
   **approve** and a **reject**. Lower-stakes actions (markdowns, disposals,
   shelf resets) don't gate on approval: the copilot applies them and tells the
   manager the floor team has been notified.

It's all driven by operational and inventory **data** — no image recognition,
nothing fabricated.

> Branding note: this is intentionally generic ("Store #2711"). It works as-is
> for any convenience / grocery / c-store retailer.

---

## Setup (do once before the demo)

```bash
cd agents/store-manager-agent

# 1) Start the local stack (about 30s on a warm machine)
agentic dev up

# 2) Seed the store's operational + inventory data (Mongo)
docker exec -w /app/agents/store-manager-agent \
  -e PYTHONPATH=/app/agents/store-manager-agent/src \
  store-manager-agent-app-1 \
  /app/.venv-aer-tool/bin/python -m store_manager_agent.seed
```

Open the playground at **http://localhost:3000** and pick
**store-manager-agent**. Keep the **User ID** field at a fixed value for the
whole demo (the default is fine) — the memory and cross-session acts depend on
it staying the same.

**Then seed memory** (one line in the chat):

```
Seed store memory.
```

You should see a confirmation that semantic facts, taxonomic terms, procedures,
and an episode were written.

> **Pre-flight check:** type *"Give me the morning rundown for store 2711"* —
> you should get a short list of issues (low water, a void spike, a freezer
> alarm) within a few seconds. If not, see Troubleshooting at the bottom.

> **Reset between rehearsals:** click **New Session** for a fresh thread. To
> reset the data, re-run the seed command above (it's idempotent — it clears
> prior purchase orders and markdowns). For a totally fresh memory slate, just
> change the **User ID**.

---

## Talk track

### Opening (~30s)

> *"This is a copilot for a store manager. Think of it as the assistant manager
> who's read every report, knows this specific store, and never forgets a
> decision — but always checks with you before spending money or changing the
> floor. Everything it says is grounded in the store's actual operational and
> inventory data. I'll walk through a morning shift."*

---

### Act 1 — Morning rundown *(~90s)* — semantic + taxonomic memory

#### Prompt 1
```
Give me the morning rundown for store 2711 — what needs my attention today?
```

**Expect:** a short prioritized list — bottled water was out of stock
overnight, a void spike on register 2, expired egg-salad waste, and a
high-temp alarm on freezer #3.

> *"Two kinds of memory are already in play. It pulled this store's profile
> from **semantic memory** — it knows 2711 is a campus store with heavy
> late-night demand and that freezer #3 is unreliable, so the freezer alarm
> isn't noise, it's a known risk. And when it uses a term like 'dead SKU' or
> 'near-expiry', those definitions come from **taxonomic memory** — a shared
> dictionary every store in the chain uses the same way."*

---

### Act 2 — Reorder before a heatwave: HITL approve *(~3 min, headline #1)*
— episodic + procedural memory

> *"First real decision of the day: restocking."*

**New Session** (keep the same User ID).

#### Prompt 2
```
Water's running low and there's a heatwave coming. What should I reorder,
and how much?
```

**🎯 The memory moment.** Expect the copilot to:
- recommend a bottled-water order, sized up for the heatwave;
- **bring up the past lesson unprompted** — something like *"last heatwave we
  under-ordered water and sold out for about four hours"*;
- flag that the order is **over the $250 approval limit** and ask whether to
  submit it.

> *"Notice what it just did. It recalled a specific past event — the last
> heatwave stockout — from **episodic memory**. Nobody typed that; it remembered
> it. Then it sized the order using the **reorder SOP** from **procedural
> memory** — par level, scaled by the forecast multiplier. That's three of the
> four memory types in one answer: a fact about the store, a lesson from the
> past, and the procedure to follow."*

> **Crucially: it has NOT paused yet.** *"It told me the order needs approval
> and asked first — it didn't silently freeze. That heads-up before the pause is
> enforced by the tool itself, not by hoping the model behaves."*

#### Prompt 3
```
Yes, submit that water purchase order for approval.
```

**🎯 Now the suspend.** The UI shows the execution **Suspended** with
**Approve / Reject** buttons.

> *"Now we're paused for a human. In a real deployment this routes to whoever
> owns the budget — a Slack message, an email, an approval queue. The copilot
> didn't place the order; it submitted it."*

#### **Click *Approve***

**Expect:** the copilot resumes and confirms the order is placed, with a real
`PO-XXXXXXXX` reference.

> *"On approval it calls a *separate* tool to actually place the order — the
> mutation only happens after the human says yes — and it records the outcome to
> episodic memory, so next heatwave it'll remember this too."*

---

### Act 3 — Expiring & dead stock: act directly, notify the team *(~2 min)*
— the right action gets the right amount of friction

> *"Next: waste. The copilot watches what's about to expire and what's gone
> dead on the shelf. Unlike a purchase order, a markdown or a disposal isn't a
> spend that needs sign-off — so the copilot just does it and tells the team."*

**New Session.**

#### Prompt 4
```
The turkey club sandwiches are near expiry. Mark them down.
```

**Expect:** the copilot applies the markdown directly (e.g. ~50% off) per the
markdown SOP, gives an `MD-` reference, and says the floor team will be notified
to re-tag the shelf. **No suspend, no Approve/Reject buttons.**

#### Prompt 5
```
The egg salad sandwiches are expired. Dispose of them.
```

**Expect:** the copilot records the disposal (another `MD-` reference) and says
it's notifying the floor team to pull the items. Again, **no suspend.**

> *"Notice the difference from the reorder. A purchase order spends money, so it
> paused for you. A markdown or a disposal is routine floor work — so the copilot
> applies it immediately and routes the task to the team rather than making you
> click approve on every sandwich. The friction matches the stakes."*

---

### Act 4 — Planogram / shelf compliance: dispatch the reset *(~2 min)*

> *"Merchandising. Is the shelf actually set the way corporate intends? No
> cameras here — this is pure data: the copilot compares what's *observed* on
> the shelf against the approved planogram."*

**New Session.**

#### Prompt 6
```
Check shelf BEV-COOLER-1 against our planogram and give me a reset list.
```

**Expect:** two violations — bottled water is under-faced (2 facings instead of
4), and gift cards are sitting in the energy-drink slot (off-plan) — plus a
concrete reset task list per the planogram-reset SOP.

#### Prompt 7
```
Go ahead and apply that planogram reset.
```

**Expect:** the copilot dispatches the reset as a work order and confirms the
floor team has been notified. **No suspend.**

> *"A shelf reset is floor work, not a spend — so like the markdown, the copilot
> dispatches it straight to the team as a work order and tells you it's done. The
> purchase order earlier was the one thing that needed your sign-off."*

---

### Act 5 — Cross-session memory: a new shift *(~2 min, headline #2)*

> *"Last thing. Everything so far was one continuous session. Real managers come
> and go across shifts. Let me start completely fresh and see what the copilot
> remembers."*

#### **Click "New Session"** — fresh thread, **same User ID**

#### Prompt 8
```
I'm back for my next shift. Before anything else, recap what we already
decided last time — the orders I placed and the markdowns I made —
with their reference numbers.
```

**🎯 The recall moment.** Expect the copilot to lead with a **summary of the
specific decisions from the earlier session** — the water PO and the turkey-club
markdown, each with its **real reference number** — and only then layer on the
store facts to watch (the flaky freezer). It should NOT just re-run a generic
morning rundown.

> *"Fresh thread, no scrollback — and it leads with what I actually did last
> shift: the water purchase order and the markdown, by their real reference
> numbers, because those decisions were stamped to this manager and saved to
> memory. That's the difference between a chatbot that resets every conversation
> and a copilot that picks up where you left off."*

> If the copilot ever gives only a generic rundown instead of the decision
> recap, prompt it once more: *"No — list the specific purchase orders and
> markdowns I made last session, with their references."*

#### Optional — show the scoping (nice, ~30s)

> *"Watch what's personal vs. shared."* Change the **User ID** to something else
> (e.g. `someone-else`) and ask:

```
What purchase orders and markdowns have I made?
```

**Expect:** nothing — this user has no history.

```
What's a dead SKU, and what's our reorder procedure?
```

**Expect:** it still answers — definitions and SOPs are **org-wide**.

> *"So the personal memory — your decisions, your store facts — is scoped to the
> manager and resets cleanly when a new person logs in. But the shared knowledge
> — what a 'dead SKU' means, how reordering works — persists for everyone.
> That's also how you get a clean demo or a fresh deployment: new user ID, no
> cleanup needed."*

---

### Act 6 — Procedural memory: the copilot learns your routine *(~2.5 min, headline #3)*

> *"One more. So far the copilot remembers facts and past decisions. The most
> powerful kind of memory is procedural — it can learn how YOU like to work.
> Watch."*

**New Session**, same User ID.

#### Prompt 9
```
Give me this morning's overnight report.
```

**Expect:** the base report (sales, out-of-stocks, voids, the freezer alarm,
waste). Note it does NOT yet include expiring items or a planogram check.

#### Prompt 10 *(the manager asks for more, as they would on a real shift)*
```
What about expiring items?
```

#### Prompt 11
```
And check the planogram on BEV-COOLER-1.
```

> *"So this manager always wants three things in their morning report: the base
> numbers, what's expiring, and whether the shelf is set right. Today I had to
> ask for the last two. Let me tell the copilot to remember that."*

#### Prompt 12 *(teach it the routine — use this explicit phrasing)*
```
From now on, whenever I ask for the overnight report, also include expiring
items and a planogram check. Save that as my routine.
```

**Expect:** the copilot confirms it saved the routine (procedural memory).

#### **Click "New Session"** — fresh thread, **same User ID**

#### Prompt 13 *(the payoff — ask for ONLY the base report)*
```
Give me this morning's overnight report.
```

**🎯 The learning moment.** Expect the copilot to return the base report **plus
an Expiring/Expired section AND a Shelf-Compliance section — without being
asked** — noting it included them because they're part of your saved routine.

> *"That's procedural memory. I taught it my routine once; now every future
> shift it runs my whole rundown automatically. And like the other personal
> memory, it's scoped to me — a different manager still gets the plain report
> until they teach it their own routine. This is the difference between a tool
> you operate and a copilot that adapts to how each manager works."*

> Reliability tip: use the explicit "save that as my routine" phrasing in
> Prompt 12. If you instead just ask the follow-ups and wait for the copilot to
> offer, it sometimes offers a shelf reset rather than offering to save the
> routine — the explicit save is deterministic.

---

### Closing (~30s)

> *"To recap: one copilot that reads the overnight report, sizes reorders,
> manages waste, and audits the shelf — all from the store's real data. It uses
> four kinds of memory: facts about the store, lessons from past shifts, shared
> definitions and procedures, and — as we just saw — it learns each manager's
> own routine. And it puts the friction where it belongs: spending money over
> the limit pauses for your approval, while routine floor work — markdowns,
> disposals, shelf resets — it just handles and notifies the team."*

---

## Quick prompt reference (copy/paste)

| # | Act | Prompt |
|---|-----|--------|
| — | Setup | `Seed store memory.` |
| 1 | 1 | `Give me the morning rundown for store 2711 — what needs my attention today?` |
| 2 | 2 | `Water's running low and there's a heatwave coming. What should I reorder, and how much?` |
| 3 | 2 | `Yes, submit that water purchase order for approval.` → **Approve** |
| 4 | 3 | *(New Session)* `The turkey club sandwiches are near expiry. Mark them down.` → applied, team notified |
| 5 | 3 | `The egg salad sandwiches are expired. Dispose of them.` → applied, team notified |
| 6 | 4 | *(New Session)* `Check shelf BEV-COOLER-1 against our planogram and give me a reset list.` |
| 7 | 4 | `Go ahead and apply that planogram reset.` → dispatched, team notified |
| 8 | 5 | *(New Session, same User ID)* `I'm back for my next shift. Before anything else, recap what we already decided last time — the orders I placed and the markdowns I made — with their reference numbers.` |
| 9 | 6 | *(New Session)* `Give me this morning's overnight report.` |
| 10 | 6 | `What about expiring items?` |
| 11 | 6 | `And check the planogram on BEV-COOLER-1.` |
| 12 | 6 | `From now on, whenever I ask for the overnight report, also include expiring items and a planogram check. Save that as my routine.` |
| 13 | 6 | *(New Session, same User ID)* `Give me this morning's overnight report.` → auto-includes expiring + planogram |

---

## Failure modes & recovery

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| First prompt errors with `'"human_review"'` | Stale code before the `format`→`replace` fix | `agentic dev restart` |
| "No store profile found" / empty rundown | Store data not seeded | Re-run the seed command from Setup |
| Rundown is generic, doesn't mention freezer #3 / past heatwave | Memory not seeded, or User ID changed | Send `Seed store memory.` and keep the User ID fixed |
| Reorder doesn't recall the past stockout | Episodic memory not seeded under this user, or index still catching up | Re-send `Seed store memory.`; wait a few seconds (vector index lag) and retry |
| PO places without pausing | Order came in under the $250 limit | Use the water prompt verbatim — the heatwave multiplier pushes it over |
| HITL pause shows no Approve/Reject buttons | Old playground UI image | `agentic dev clean && agentic dev up` |
| Copilot suspends on Prompt 2 with no message | Approval tool returned a suspend on the first call | Confirm `tools.py` has the `manager_confirmed` two-step |
| Markdown/disposal/planogram suspends for approval | Stale code before the direct-apply change | `agentic dev restart` — only the PO tool should suspend now |
| Cross-session recall finds nothing | User ID changed between sessions | Keep the User ID fixed across all acts |

---

## Backup: regression test

If something looks off mid-presentation, the automated `demo.py` runs every
section (including the PO approve/reject paths, direct-apply markdown/disposal
and planogram, cross-session recall, idempotency, and the learned rundown
routine) in a couple of minutes and exits non-zero on any failure:

```bash
uv run python demo.py --reset
```

It hits the same endpoints the playground hits and asserts on user-visible
behavior.
