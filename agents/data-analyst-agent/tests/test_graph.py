from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command


def test_graph_runs_canonical_analytics_flow_with_artifacts() -> None:
    from data_analyst_agent import main

    result = main.build_agent().invoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "Compare loss frequency across one, two, and three pedal vehicles, "
                        "controlling for age, zip, and annual mileage. Show me how the cohort "
                        "mix has changed over time."
                    )
                )
            ]
        },
        config={"configurable": {"thread_id": "analytics-test-thread"}},
    )

    response = result["messages"][-1]
    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert isinstance(response, AIMessage)
    assert result["flow_sequence"] == ["analytics_flow"]
    assert [message.name for message in tool_messages if message.name != "task"] == [
        "compare_loss_frequency",
        "cohort_mix_over_time",
        "plot_loss_frequency_chart",
        "plot_cohort_mix_trend_chart",
    ]
    assert any(
        message.name == "task" and "Executed analytics" in str(message.content)
        for message in tool_messages
    )
    assert "claims per 1000 policies" in response.content
    assert "artifact_ids" not in response.additional_kwargs
    assert [artifact["id"] for artifact in response.additional_kwargs["artifacts"]] == [
        "artifact-chart-loss-frequency",
        "artifact-chart-cohort-mix",
    ]
    assert [artifact["kind"] for artifact in response.additional_kwargs["artifacts"]] == [
        "chart",
        "chart",
    ]


def test_graph_replays_learned_procedure_through_review_with_tool_messages(monkeypatch) -> None:
    from data_analyst_agent import graph
    from data_analyst_agent import main

    monkeypatch.setattr(graph, "matches_procedural_memory", lambda app, query: True)

    result = main.build_agent().invoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "Compare loss frequency across one, two, and three pedal vehicles, "
                        "controlling for age, zip, and annual mileage. Show me how the cohort "
                        "mix has changed over time."
                    )
                )
            ]
        },
        config={"configurable": {"thread_id": "procedure-replay-test-thread"}},
    )

    tool_names = [
        message.name for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert result["flow_sequence"] == [
        "analytics_flow",
        "investigation_flow",
        "interpretation_flow",
    ]
    assert "compare_loss_frequency" in tool_names
    assert "cohort_mix_over_time" in tool_names
    assert "plot_loss_frequency_chart" in tool_names
    assert "plot_cohort_mix_trend_chart" in tool_names
    assert "get_claim_narratives" in tool_names
    assert result["__interrupt__"][0].value["suspend_context"]["decision_type"] == (
        "rating_recommendation"
    )


def test_graph_routes_recommendation_followup_to_native_human_review() -> None:
    from data_analyst_agent import main

    result = main.build_agent().invoke(
        {"messages": [HumanMessage(content="What rating action do you recommend?")]},
        config={"configurable": {"thread_id": "review-test-thread"}},
    )

    interrupts = result["__interrupt__"]
    payload = interrupts[0].value

    assert payload["suspend_reason"] == "awaiting_human_review"
    assert payload["suspend_context"]["review_presentation"]["schema_version"] == (
        "review-presentation/v1"
    )
    assert payload["suspend_context"]["guardrail_triggered"] is False


def test_graph_resumes_approved_review_and_writes_decision() -> None:
    from data_analyst_agent import main

    graph = main.build_agent()
    config = {"configurable": {"thread_id": "approval-test-thread"}}
    first = graph.invoke(
        {"messages": [HumanMessage(content="What rating action do you recommend?")]},
        config=config,
    )
    assert first["__interrupt__"][0].value["suspend_reason"] == "awaiting_human_review"

    result = graph.invoke(
        Command(resume={"decision": "approve", "notes": "approved for demo"}),
        config=config,
    )

    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert any(message.name == "write_audit_log" for message in tool_messages)
    assert "Decision written to audit_log with status approved" in result["messages"][-1].content


def test_graph_resumes_rejected_review_without_writeback() -> None:
    from data_analyst_agent import main

    graph = main.build_agent()
    config = {"configurable": {"thread_id": "rejection-test-thread"}}
    first = graph.invoke(
        {"messages": [HumanMessage(content="What rating action do you recommend?")]},
        config=config,
    )
    assert first["__interrupt__"][0].value["suspend_reason"] == "awaiting_human_review"

    result = graph.invoke(
        Command(resume={"decision": "reject", "notes": "needs more evidence"}),
        config=config,
    )

    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert not any(message.name == "write_audit_log" for message in tool_messages)
    assert "Reviewer decision: reject" in result["messages"][-1].content
    assert "needs more evidence" in result["messages"][-1].content


def test_build_agent_uses_agent_yaml_llm_config_when_provider_key_exists(monkeypatch) -> None:
    from data_analyst_agent import main

    sentinel_model = object()
    captured_model = None

    def fake_build_runtime_llm(temperature: float = 0):
        assert temperature == 0
        return sentinel_model

    def fake_app_llm(model):
        nonlocal captured_model
        captured_model = model
        return None

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(main, "_build_runtime_llm", fake_build_runtime_llm)
    monkeypatch.setattr(main.app, "llm", fake_app_llm)

    main.build_agent()

    assert captured_model is sentinel_model
