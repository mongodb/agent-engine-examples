# Holiday Assistant — Live Demo Script

A 10–12 minute walkthrough you can run live in front of an audience using
the playground UI. The script covers five headline capabilities:

1. **Hotel search & booking** (semantic search + structured booking)
2. **Instant cancellation** (the common case)
3. **Boutique-hotel cancellation with hotel approval** — the human-in-the-loop
   flow where the **hotel** is the reviewer
4. **Cross-session memory** — book in one conversation, recall in another
   ("my trip to Paris")
5. **Cross-domain knowledge** (transport, policy)

The HITL section is the headline moment for the platform's
suspend/resume primitive. The cross-session memory act is the headline
moment for traveler identity — bookings are stamped with `user_id` at
create time and surface across threads when the same user comes back.

---

## Setup (do once before the demo)

```bash
cd agents/holiday-assistant-agent

# 1) Start the local stack (about 30s on a warm machine)
agentic dev up

# 2) Seed the holiday MongoDB (only needed once per cluster)
docker exec -w /app/agents/holiday-assistant-agent \
  -e PYTHONPATH=/app/agents/holiday-assistant-agent/src \
  holiday-assistant-agent-app-1 \
  /app/.venv-aer-tool/bin/python -m holiday_assistant_agent.seed
```

Open the playground at **http://localhost:3000** and pick **holiday-assistant-agent**.

> **Pre-flight check:** type *"Find me a hotel in Barcelona"* — you should
> get a list of two 4-star options within a few seconds. If not, see the
> Troubleshooting section at the bottom.

> **Reset between rehearsals:** click **New Session** in the UI to start a
> fresh thread. To wipe seeded bookings entirely, drop the bookings
> collection: `docker exec mongodb-atlas-local mongosh --quiet --eval
> "db.getSiblingDB('holiday_db').bookings.drop()"`.

---

## Talk track

### Opening (~30s)

> *"This is a multi-skill holiday concierge backed by MongoDB. It does
> hotel search, transport advice, policy lookups, bookings and
> cancellations — all in one agent, no manual hand-offs between
> specialists. The interesting bit today is how it handles cancellations
> when a hotel insists on approving them itself."*

---

### Act 1 — Search & book *(~90s)*

#### Prompt 1
```
Find me a 4-star hotel in Barcelona under 200 EUR per night.
```

**Expect:** two hotels (Catalonia Barcelona Plaza at €145, Hilton Barcelona
at €169), with amenities and distances.

> *"Semantic search over MongoDB Atlas Vector Search — the hotels and
> their descriptions are pre-embedded, so we can answer fuzzy queries like
> this without any keyword matching."*

#### Prompt 2
```
Great — book the standard room at Hotel Catalonia Barcelona Plaza for Alice
Smith (alice@example.com), 1 guest, check-in 10 August 2026, check-out 12
August 2026, 145 EUR per night, breakfast included. Go ahead and book.
```

**Expect:** confirmation with an 8-character booking reference (e.g.
`AB12CD34`). **Copy the ref to your clipboard** — you'll need it.

> *"That's a real document in MongoDB now. The reference is what we'll
> use to cancel."*

#### Prompt 3 *(uses the ref from Prompt 2)*
```
Cancel booking <REF> — change of plans.
```

**Expect:** *"Your booking <REF> has been cancelled."* Single turn, no
pause.

> *"For most hotels, cancellation is instant — no human in the loop, no
> external call. The agent just updates the booking status."*

---

### Act 2 — Boutique-hotel cancellation: HITL *(~3 min, the headline)*

> *"Now the interesting case. Some hotels — typically luxury or boutique
> properties — have bespoke cancellation handling. They want to approve
> cancellations themselves rather than let the agent do it. Le Meurice
> Paris is one of those."*

**Click "New Session"** to start a clean thread.

#### Prompt 4
```
Book a deluxe room at Le Meurice Paris for Bob Jones (bob@example.com),
2 guests, check-in 5 September 2026, check-out 8 September 2026, 980 EUR
per night, no breakfast. Go ahead and book.
```

**Expect:** confirmation with a new booking reference. **Copy it.**

#### Prompt 5 *(uses the new ref)*
```
Actually, I need to cancel booking <REF>.
```

**🎯 This is the key moment.** Expect the agent to reply with something
like:

> *"Le Meurice Paris handles cancellations directly, so they'll need to
> approve this. Want me to submit the request to them on your behalf?"*

> **Crucially: it has NOT paused yet.** Compare with the broken behavior:
> *"Without this guard, the agent would silently call the cancel tool, the
> execution would suspend mid-call, and the user would just see a spinner
> with no explanation. That's the bug we fixed — the tool itself now
> refuses to suspend until the user has been informed and confirms."*

#### Prompt 6
```
Yes, please go ahead and submit the cancellation request for <REF> to
Le Meurice on my behalf.
```

**🎯 Now the suspend.** The UI will show the execution status as
**Suspended** and surface **Approve / Reject** buttons.

> *"Now we're paused. In a real deployment, this is where the platform
> would ping the hotel — Slack message, email, web hook, whatever. The
> reviewer in this flow IS the hotel, not an internal compliance person.
> When the hotel responds, the agent resumes."*

> *"Notice the agent didn't claim the booking is cancelled — it
> correctly says the request has been submitted and the hotel has been
> contacted. That distinction matters: until the hotel says yes, the
> booking is still on."*

#### **Click *Approve*** in the UI

**Expect:** the agent resumes and replies with something like:

> *"Le Meurice has accepted the cancellation and your booking has been
> cancelled (reference <REF>)."*

> *"Two things to highlight: (1) the agent calls a *separate* tool —
> `cancel_booking_approved` — to actually mutate the database, only
> after the approval comes through. (2) it relays the hotel's decision
> rather than parroting 'request submitted' a second time."*

---

### Act 3 — HITL rejection *(~90s — show the unhappy path)*

> *"What if the hotel says no?"*

**New Session**, then:

#### Prompt 7
```
Book a deluxe room at Katikies Santorini for Carol Davis
(carol@example.com), 2 guests, check-in 1 July 2026, check-out 5 July
2026, 720 EUR per night, breakfast included. Go ahead and book.
```

#### Prompt 8 *(use the new ref)*
```
Cancel booking <REF> please.
```

> *"Same heads-up — Katikies handles cancellations directly."*

#### Prompt 9
```
Yes, please go ahead and submit the cancellation request for <REF>
to Katikies on my behalf.
```

#### **Click *Reject*** in the UI (optionally type a reason like *"Within
the no-cancellation window"*)

**Expect:** *"Katikies has rejected the cancellation request… would you
like to explore options like rescheduling or partial credit?"* — and the
booking stays `CONFIRMED`.

> *"Two things again: (1) the agent does NOT call the
> `cancel_booking_approved` tool when the decision is reject — that's
> guarded by the system prompt. (2) it pivots to offering alternatives
> rather than just leaving the user stuck."*

---

### Act 4 — Cross-session memory: "my trip to Paris" *(~2 min)*

> *"So far we've shown one conversation at a time. Real travelers come
> back later — usually mid-trip planning, often weeks later — and
> reasonably expect the agent to know what they already booked. Let's
> show what that looks like."*

**Important:** keep the **User ID** field at the top of the playground set
to the same value for the next three sessions (the default
`local-dev-user` is fine — just don't change it). Click **New Session**
between each prompt to make the thread switch obvious.

#### Prompt 10 *(book a Paris stay — copy the ref)*
```
Book a deluxe room at Le Meurice Paris for Charlie Xu (charlie@example.com),
1 guest, check-in 10 October 2026, check-out 13 October 2026, 980 EUR per
night, no breakfast. Go ahead and book.
```

**Expect:** confirmation with a booking reference. **Note the ref but
don't mention it again** — the next prompts won't reference it.

#### Prompt 11 *(book a Barcelona stay too, same trip planning context)*
```
Also book the standard room at Hotel Catalonia Barcelona Plaza for
Charlie Xu (charlie@example.com), 1 guest, check-in 5 December 2026,
check-out 8 December 2026, 145 EUR per night, breakfast included.
Go ahead and book.
```

> *"Two confirmed bookings stamped against this user. Now I'll close the
> session, come back to a totally fresh thread, and just ask casually."*

#### **Click "New Session"** — fresh thread, same User ID

#### Prompt 12
```
Can you remind me about my trip to Paris?
```

**🎯 The recall moment.** Expect the agent to call `list_my_bookings`
(or `recall_traveler_context`) with `destination: "paris"`, find the Le
Meurice booking by `user_id`, and reply with the actual booking
reference, dates, and price — *not* invented values.

> *"This is not memory — there's no embedding lookup, no LLM
> hallucination. The agent queries the bookings collection in MongoDB
> filtered by the platform's user_id, which we stamped onto the booking
> document at create time. So 'my trip to Paris' deterministically
> resolves to that ONE document. References, dates, prices are real."*

> *"There's also a memory layer running in parallel — every booking
> auto-writes a short episodic memory tagged with the hotel name and
> booking type. So even if the database query came back empty, semantic
> search over those episodes would still surface the trip when you
> mention Paris. The two systems back each other up."*

#### **Click "New Session"** again

#### Prompt 13
```
What reservations do I have?
```

**Expect:** both the Le Meurice and Hotel Catalonia bookings, listed
with their real refs.

> *"Same mechanism — `list_my_bookings` with no filter. The agent
> doesn't have to guess: it asks the database. And because the user_id
> is what binds bookings to the traveler, a different user typing the
> exact same question would correctly get nothing back."*

> *"Optional follow-up to underline the point: try changing the User ID
> in the playground header to something else like 'someone-else' and
> ask the same question. The agent will tell you it has no
> reservations — exactly the right answer."*

---

### Act 5 — Cross-domain knowledge *(~60s, optional, depending on time)*

**New Session**.

#### Prompt 14
```
How do I get from London to Barcelona on a budget? Compare options.
```

**Expect:** structured comparison of flight / train / coach with rough
prices, journey times and a recommendation.

> *"Same agent, same graph, no specialist hand-off. Transport advice is
> synthesized by the LLM with policy hints from MongoDB."*

#### Prompt 15 *(optional)*
```
What is your cancellation policy?
```

**Expect:** the seeded refundable / non-refundable terms.

> *"This is a vector search hit on the policy collection — the agent
> grounds the answer in the company's actual policy documents rather
> than making it up."*

---

### Closing (~30s)

> *"To recap: one MongoDB-backed agent doing hotel search, structured
> booking, transport synthesis and policy retrieval. Two things make
> the experience feel real. First, the HITL handoff for boutique-hotel
> cancellations: the contract is enforced by the tool itself, not by
> prompt engineering, so the agent literally cannot suspend without
> first telling the user what's going on. Second, traveler identity:
> bookings are stamped with user_id at create time, so the agent can
> recall a returning user's reservations across sessions without
> relying on memory or asking them to type a reference number."*

---

## Quick prompt reference (copy/paste)

| # | Act | Prompt |
|---|-----|--------|
| 1 | 1 | `Find me a 4-star hotel in Barcelona under 200 EUR per night.` |
| 2 | 1 | `Great — book the standard room at Hotel Catalonia Barcelona Plaza for Alice Smith (alice@example.com), 1 guest, check-in 10 August 2026, check-out 12 August 2026, 145 EUR per night, breakfast included. Go ahead and book.` |
| 3 | 1 | `Cancel booking <REF> — change of plans.` |
| 4 | 2 | `Book a deluxe room at Le Meurice Paris for Bob Jones (bob@example.com), 2 guests, check-in 5 September 2026, check-out 8 September 2026, 980 EUR per night, no breakfast. Go ahead and book.` |
| 5 | 2 | `Actually, I need to cancel booking <REF>.` |
| 6 | 2 | `Yes, please go ahead and submit the cancellation request for <REF> to Le Meurice on my behalf.` |
| 7 | 3 | `Book a deluxe room at Katikies Santorini for Carol Davis (carol@example.com), 2 guests, check-in 1 July 2026, check-out 5 July 2026, 720 EUR per night, breakfast included. Go ahead and book.` |
| 8 | 3 | `Cancel booking <REF> please.` |
| 9 | 3 | `Yes, please go ahead and submit the cancellation request for <REF> to Katikies on my behalf.` |
| 10 | 4 | `Book a deluxe room at Le Meurice Paris for Charlie Xu (charlie@example.com), 1 guest, check-in 10 October 2026, check-out 13 October 2026, 980 EUR per night, no breakfast. Go ahead and book.` |
| 11 | 4 | `Also book the standard room at Hotel Catalonia Barcelona Plaza for Charlie Xu (charlie@example.com), 1 guest, check-in 5 December 2026, check-out 8 December 2026, 145 EUR per night, breakfast included. Go ahead and book.` |
| 12 | 4 | *(New Session)* `Can you remind me about my trip to Paris?` |
| 13 | 4 | *(New Session)* `What reservations do I have?` |
| 14 | 5 | `How do I get from London to Barcelona on a budget? Compare options.` |
| 15 | 5 | `What is your cancellation policy?` |

---

## Failure modes & recovery

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| First prompt errors out with `'"human_review"'` | Stale code before the `format`→`replace` fix | `agentic dev restart` |
| "No matching hotels found" | DB not seeded | Re-run the seed command from Setup |
| Agent asks for missing booking details | Prompt got too vague — be explicit | Use the prompts above verbatim, including price |
| HITL pause shows no Approve/Reject buttons | Old playground UI image | `agentic dev clean && agentic dev up` |
| Agent suspends on Prompt 5 with no message | `cancel_booking` returning suspend on first call | Confirm `tools.py` has `user_confirmed_hotel_contact` |
| Booking refs collide between rehearsals | Same MongoDB across runs | Drop the `bookings` collection (see Setup) |
| "My trip to Paris" returns no booking | User ID changed between sessions, or booking lacks `user_id` | Confirm the User ID field in the playground hasn't changed; bookings created before this build have no user_id and won't surface — drop and rebook |

---

## Backup: regression test

If something looks off mid-presentation and you want to quickly verify the
agent is healthy without losing your place, the automated `demo.py` exercises
all five sections in about 3 minutes:

```bash
uv run python demo.py
```

It hits the same endpoints the playground hits, asserts on user-visible
behavior, and exits non-zero on any failure.
