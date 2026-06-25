---
name: eval-insurance-agent-local-ui
description: Validate the locally deployed insurance agent through the full local platform UI using Chrome DevTools MCP. Uses Atlas login, project-scoped Playground navigation, workspace selection, policy creation, claim flow, admin review approval, and final resolution. Use when the user wants to test the local browser UI, Playground flow, or admin reviews for the insurance agent.
---

# Eval: Insurance Agent (Local UI)

Use this skill when validating the insurance agent through the full local platform UI rather than direct OE requests.

This flow verifies:

- Atlas login to the local platform
- project-scoped Playground navigation
- workspace selection for `insurance-agent`
- policy flow in chat
- cross-session memory behavior
- human-review behavior in the UI
- approval from the admin reviews page
- resolved claim rendering back in the chat session

## Preconditions

- The local platform UI is already running at `UI_URL`, typically `http://localhost:30050`.
- The locally deployed Insurance Agent is visible in Playground as workspace `insurance-agent`.
- The `chrome_devtools` MCP server is available.
- The user can complete the Atlas OIDC login manually when prompted.

## Resolve `UI_URL`

- If the user provided a local UI URL or port, use that.
- Otherwise, default to `http://localhost:30050`.
- If `30050` does not load, inspect active local terminals/logs or ask the user for the actual UI URL.

This skill does **not** start the local full platform automatically unless the user explicitly asks.

## Browser tools

Use the `chrome_devtools` MCP tools directly for all browser work.

Primary tools:

- `list_pages`
- `select_page`
- `navigate_page`
- `take_snapshot`
- `click`
- `fill`
- `press_key`
- `wait_for`

## Critical interaction rules

### Always use fresh snapshots

- After navigation, modal changes, or other structural UI changes, call `take_snapshot` again before using `uid`s.
- Do not reuse stale `uid`s.

### Atlas login requires user handoff

- Default to `Login with Atlas`.
- After clicking `Login with Atlas`, stop and ask the user to complete the OIDC flow manually.
- Resume only after the user confirms the browser is back in the local app.
- Do **not** use the local `admin` / `admin` login path as the default flow.

### React input workaround

The chat input is React-controlled. After calling `fill`, trigger a real key event:

1. `fill`
2. `press_key("Backspace")`
3. `take_snapshot`
4. verify Send is enabled
5. `click` Send

This removes the last character, but it reliably updates React state. If needed, type the final character again before sending.

### Current UI shape

- After Atlas login, the app usually lands on `{UI_URL}/project/<group_id>/overview`.
- Use the left-sidebar `Playground` button or the project-scoped Playground route rather than `/agent`.
- Playground currently shows a workspace dropdown before the chat interface appears.
- `New Session` resets the chat, but the URL may stay on `/project/<group_id>/playground`. Do not rely on session ids in the URL.

### Timing

- After chat sends, wait 2-5 seconds, then `take_snapshot`.
- For policy creation or claim resolution, wait longer if needed.
- For human review, wait an extra 10-15 seconds before failing.
- The admin reviews page may briefly show `0 pending reviews` before the row appears. Wait and re-snapshot once before treating that as a failure.

## Flow

### Step 1: Login With Atlas

1. `navigate_page` to `{UI_URL}/login`
2. `take_snapshot`
3. `click` `Login with Atlas`
4. Ask the user to complete the OIDC flow manually
5. After the user confirms, `take_snapshot`

Assert:

- The browser returns to the local app under `{UI_URL}`.
- The page shows project overview, Playground, or another authenticated local-app view.

### Step 2: Open Playground

1. If the app lands on project overview, `click` the sidebar `Playground` button.
2. Otherwise, navigate to the project-scoped Playground if needed.
3. `take_snapshot`

Assert:

- The page shows either the workspace selector or the chat interface.

### Step 3: Select Workspace

If the chat interface is already visible, skip this step.

Otherwise:

1. `click` the workspace selector
2. `click` `insurance-agent`
3. `take_snapshot`

Assert:

- The chat controls are visible.
- The selected workspace is `insurance-agent`.

### Step 4: Confirm Agent And User Context

1. If needed, expand `Select Agent` and verify `insurance-agent` shows `SELECTED`.
2. Only open `API Key` if the UI indicates guest mode or chat sends fail with `Forbidden`.
3. If the modal shows `Using guest session`, click `Reset`, then `Apply`.
4. Do **not** switch to `Guest` for the default validation flow.
5. `take_snapshot`

Assert:

- The chat input is visible.
- The agent is selected.
- The flow is using the authenticated Atlas context rather than guest mode.

### Step 5: New Session (Policy Flow)

1. `click` the `New Session` button with `includeSnapshot: true`
2. `take_snapshot`

Assert:

- The chat shows its empty-state / start-conversation view.
- A new conversation started even if the URL did not change.

### Step 6: Greeting

Send:

```text
Hello. I'd like to buy insurance for my new car.
```

Assert:

- The agent asks for personal info such as name, email, and phone.

### Step 7: Provide Personal Info

Send:

```text
Sure. Max Marcon, max.marcon@mongodb.com, 850522765
```

Assert:

- The conversation advances to vehicle details and/or coverage preference.
- If the agent says there was an issue saving customer information but still continues, record that as a behavioral issue and keep going.

### Step 8: Ask About Coverage Types

Send:

```text
Yes can you explain the differences between the coverage types?
```

Assert:

- The agent explains at least 2 of 3 tiers: Basic, Standard, Comprehensive.

### Step 9: Provide Vehicle And Coverage Choice

Send:

```text
It's a 2024 Ford Mustang Mach-e GT. I'll go for Comprehensive. I am 43.
```

Assert:

- The agent returns a quote with a dollar amount.

### Step 10: Create The Policy

Send:

```text
Go ahead and create the policy.
```

Wait up to 5 seconds, then snapshot again.

Assert:

- The agent confirms a policy was created and shows a `POL-...` number.

### Step 11: New Session (Claim Flow)

1. `click` the `New Session` button with `includeSnapshot: true`
2. `take_snapshot`

Assert:

- The chat resets to the empty state.

### Step 12: Report An Accident

Send:

```text
I was in an accident and the car is pretty damaged.
```

Assert:

- The agent asks follow-up claim questions.
- The agent recognizes the customer by name and/or policy.

### Step 13: Provide Claim Details

Send:

```text
This is a collision claim. The accident occurred yesterday, Feb 21. It was a single car accident. I swerved to avoid a raccoon and crashed into a tree. The mechanic estimated $15k in damages as I need to replace the front and the headlights, and there is some minor structural damage.
```

After send:

1. Wait 5 seconds, then `take_snapshot`
2. If human review is not visible yet, wait another 10 seconds and snapshot again

Assert:

- The UI shows an `Awaiting human review` state/banner.
- Claim details such as amount and risk level are visible if the UI renders them.

### Step 14: Approve Human Review

1. Open the sidebar `Pending Reviews` page or navigate to `{UI_URL}/admin/reviews`
2. Wait 1 second, then `take_snapshot`
3. If the page shows `0 pending reviews`, wait a few more seconds and snapshot again
4. Verify there is at least one pending review row
5. `click` the first `REVIEW` button with `includeSnapshot: true`
6. Verify the modal shows the claim details
7. `click` `Approve`
8. Wait 2-3 seconds, then `take_snapshot`

Assert:

- The approval is processed and the pending count decreases or the modal closes successfully.

### Step 15: Verify Resolution

1. Return to `Playground` from the sidebar
2. `take_snapshot`
3. If the claim conversation is not already visible, use `History` to reopen the latest claim thread
4. Wait 3-10 seconds if needed, then `take_snapshot`

Assert:

- The claim conversation shows a resolved/approved claim message.
- The human review pending state is no longer visible.

## Reporting

Use this summary format:

```text
## UI Flow Test Results

UI URL: http://localhost:<port>
Branch: <branch>  Commit: <sha>

| Step | Description                    | Result |
|------|--------------------------------|--------|
| 1    | Login with Atlas               | PASS/FAIL |
| 2    | Open Playground                | PASS/FAIL |
| 3    | Select Workspace               | PASS/FAIL/SKIP |
| 4    | Confirm Agent/User Context     | PASS/FAIL/SKIP |
| 5    | New Session (Policy)           | PASS/FAIL |
| 6    | Greeting                       | PASS/FAIL |
| 7    | Provide Personal Info          | PASS/FAIL |
| 8    | Ask About Coverage Types       | PASS/FAIL |
| 9    | Vehicle and Coverage Choice    | PASS/FAIL |
| 10   | Create the Policy              | PASS/FAIL |
| 11   | New Session (Claim)            | PASS/FAIL |
| 12   | Report an Accident             | PASS/FAIL |
| 13   | Provide Claim Details          | PASS/FAIL |
| 14   | Approve Human Review           | PASS/FAIL |
| 15   | Verify Resolution              | PASS/FAIL |

Overall: x/15
```

For any failure, include the step number and a short description of what blocked progress.

If a step succeeded but exposed a behavioral issue, call it out explicitly after the table.

## Troubleshooting

- If the login form never appears, confirm `UI_URL` is correct.
- If username/password login succeeds but chat sends fail with `Forbidden`, log out and retry with `Login with Atlas`.
- If Send stays disabled, repeat the React workaround and re-snapshot.
- If the workspace selector appears, choose `insurance-agent`.
- If the `API Key` modal shows `Using guest session`, click `Reset` and `Apply` before continuing.
- If `New Session` does not change the URL, that is expected in the current UI.
- If the reviews page is empty, wait a little longer and revisit the claim conversation to confirm the agent actually entered human review.
- If the claim resolution does not show up after approval, wait a bit longer and return to Playground again.
