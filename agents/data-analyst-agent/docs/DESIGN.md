# Data Analyst Agent Design

This example ports the old data-and-list demo into a standalone AtlasAP agent
without carrying over the old Guardrails dependency or trace-specific UI hooks.

## Flow Contract

- `analytics_flow`: compare loss frequency across one-, two-, and three-pedal
  vehicles and show cohort mix over time from `Customer`.
- `investigation_flow`: retrieve representative claims narratives and connect
  them back to the quantitative result.
- `interpretation_flow`: build a rating recommendation, suspend with a native
  LangGraph interrupt, and write approved decisions to `audit_log`.

## Code Organization

- `main.py` bootstraps the AtlasAP `App`, data store, tool registration, and
  entrypoint.
- `graph.py` owns the LangGraph topology and should read like the flow diagram.
- `routing.py` owns flow selection and conditional-edge routing.
- `flow_messages.py` owns low-level `AIMessage` and `ToolMessage` construction.
- `flows/analytics.py`, `flows/investigation.py`, and
  `flows/interpretation.py` own the user-visible flow behavior.
- `tools.py` owns `@app.tool` registrations.

## Platform Surface

The agent emits message artifacts for the Playground:

- `mongodb_query`
- `image`
- `chart` fallback
- `source`
- `review_summary`

The human review loop uses the interrupt payload shape:

```json
{
  "suspend_reason": "awaiting_human_review",
  "suspend_context": {
    "review_presentation": {
      "schema_version": "review-presentation/v1"
    }
  }
}
```

Guardrails fields are present only as compatibility metadata and are set to
`guardrail_triggered: false`.
