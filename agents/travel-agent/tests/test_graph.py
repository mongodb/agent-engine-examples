from __future__ import annotations

import json
import types

import pytest


def test_main_module_imports_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing the agent module must not require ORG_ID / PROJECT_ID.

    The runtime identifiers are only enforced by `_validate_env`, which runs
    inside `build_agent` and `main`. This keeps the module importable for
    tests, tooling, and `agentic dev` introspection.
    """
    monkeypatch.delenv("ORG_ID", raising=False)
    monkeypatch.delenv("PROJECT_ID", raising=False)
    monkeypatch.delenv("APP_ID", raising=False)
    import importlib

    import travel_agent.main as main_module

    importlib.reload(main_module)
    assert main_module.APP_NAME == "Travel Agent"


def test_validate_env_requires_runtime_identifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    from travel_agent import main as main_module

    monkeypatch.delenv("PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="PROJECT_ID"):
        main_module._validate_env()


def test_procedural_extractor_uses_provider_model_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from travel_agent import main as main_module

    seen: dict[str, object] = {}

    class FakeStructuredLlm:
        def invoke(self, _messages: object) -> object:
            return main_module.ProceduralExtraction(procedure=None)

    class FakeLlm:
        def with_structured_output(self, _schema: object) -> FakeStructuredLlm:
            return FakeStructuredLlm()

    def fake_build_configured_llm(temperature: float = 0.0) -> FakeLlm:
        seen["temperature"] = temperature
        return FakeLlm()

    monkeypatch.setattr(main_module, "_build_configured_llm", fake_build_configured_llm)

    main_module._save_procedural_memory(
        {"question": "Recover a cancelled flight"}, "user", "session"
    )

    assert seen == {"temperature": 0}


def test_procedural_extractor_uses_current_memory_sdk_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from travel_agent import main as main_module

    monkeypatch.setenv("APP_ID", "runtime-travel-agent")

    parsed = main_module.ProceduralExtraction(
        procedure=main_module.TRAVEL_PROCEDURE_NAME,
        description="Use for disrupted flights that need passenger recovery.",
        content="Recover the disrupted flight.",
        tags="travel,irrops",
        trigger_conditions=["Cancelled flights"],
        steps=[
            main_module.ProcedureStep(
                description="Assess disruption | handler:impact_flow",
                content="Assess disruption and prioritize passengers.",
            ),
            main_module.ProcedureStep(
                description="Rank recovery | handler:reaccommodation_flow",
                content="Review the passenger and rank compliant recovery options.",
            ),
            main_module.ProcedureStep(
                description="Complete recovery | handler:resolution_flow",
                content="Execute the approved recovery and notify the traveler.",
            ),
        ],
    )

    class FakeStructuredLlm:
        def invoke(self, _messages: object) -> object:
            return parsed

    class FakeLlm:
        def with_structured_output(self, _schema: object) -> FakeStructuredLlm:
            return FakeStructuredLlm()

    monkeypatch.setattr(main_module, "_build_configured_llm", lambda **_kwargs: FakeLlm())

    saved: dict[str, object] = {}

    class FakeMemory:
        def save_procedure(
            self,
            *,
            procedure: str,
            description: str,
            content: str,
            user_id: str | None = None,
            steps: list[dict[str, object]] | None = None,
            resources: list[dict[str, object]] | None = None,
            allowed_tools: list[str] | None = None,
            compatibility: str | None = None,
            license: str | None = None,
            trigger_conditions: list[str] | None = None,
            tags: list[str] | None = None,
            visibility: str = "private",
            agent_id: str | None = None,
            extraction_source: str | None = None,
            source_format: str | None = None,
            source_path: str | None = None,
            update_existing: bool = False,
        ) -> dict[str, object]:
            saved.update(
                procedure=procedure,
                description=description,
                content=content,
                user_id=user_id,
                steps=steps,
                resources=resources,
                allowed_tools=allowed_tools,
                compatibility=compatibility,
                license=license,
                trigger_conditions=trigger_conditions,
                tags=tags,
                visibility=visibility,
                agent_id=agent_id,
                extraction_source=extraction_source,
                source_format=source_format,
                source_path=source_path,
                update_existing=update_existing,
            )
            return {"id": "procedure-1"}

    class FakeApp:
        memory = FakeMemory()

    monkeypatch.setattr(main_module, "app", FakeApp())

    main_module._save_procedural_memory(
        {"question": "Recover a cancelled flight"}, "user-1", "session-1"
    )

    assert saved["procedure"] == main_module.TRAVEL_PROCEDURE_NAME
    assert saved["agent_id"] == "runtime-travel-agent"
    assert saved["source_path"] == "session:session-1"
    assert saved["update_existing"] is True


def test_memory_context_for_query_always_uses_configured_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from travel_agent import main as main_module

    class FakeMemory:
        def build_context(self, **kwargs: object) -> object:
            assert kwargs == {
                "query": "cancelled flight recovery",
                "user_id": "user-1",
                "thread_id": "session-1",
            }
            return types.SimpleNamespace(
                formatted_context="remembered context", selected_memories=[]
            )

    class FakeApp:
        memory = FakeMemory()
        _runtime = types.SimpleNamespace(memory_engine=None)

    monkeypatch.setattr(main_module, "app", FakeApp())

    memory_context, citation_index = main_module._memory_context_for_query(
        "cancelled flight recovery",
        "user-1",
        "session-1",
    )

    assert memory_context == "remembered context"
    assert citation_index == ""


def test_execution_user_id_prefers_runtime_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    from travel_agent import main as main_module

    monkeypatch.setattr(main_module, "get_current_user_id", lambda: "runtime-user")

    assert (
        main_module._execution_user_id({"messages": [], "user_id": "state-user"}) == "runtime-user"
    )


def test_queue_procedural_learning_uses_runtime_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from travel_agent import main as main_module

    submitted: dict[str, object] = {}

    class FakeExecutor:
        def submit(self, fn: object, payload: object, user_id: str, session_id: str) -> None:
            submitted.update(
                fn=fn,
                payload=payload,
                user_id=user_id,
                session_id=session_id,
            )

    monkeypatch.setattr(main_module, "get_current_user_id", lambda: "runtime-user")
    monkeypatch.setattr(main_module, "get_current_session_id", lambda: "runtime-session")
    monkeypatch.setattr(main_module, "_PROCEDURE_EXECUTOR", FakeExecutor())
    monkeypatch.setattr(main_module, "_emit_oe_node", lambda *args, **kwargs: None)

    state = {
        "messages": [],
        "user_id": "state-user",
        "session_id": "state-session",
        "travel_context": {},
    }
    travel_context = {
        "resolution_completed": True,
        "selected_pnr": "TRV-00421",
        "recommended_option": {"option_id": "ALT-1"},
        "disruption_id": "DISR-1001",
    }

    main_module._queue_procedural_learning(state, travel_context, pnr="TRV-00421")

    assert submitted["user_id"] == "runtime-user"
    assert submitted["session_id"] == "state-session"
    assert travel_context["procedural_learning_queued"] is True


def test_discover_replay_procedure_uses_org_visibility(monkeypatch: pytest.MonkeyPatch) -> None:
    from travel_agent import main as main_module

    calls: list[tuple[str, dict[str, object]]] = []

    class FakeMemory:
        def discover_procedures(self, **kwargs: object) -> list[dict[str, object]]:
            calls.append(("discover", kwargs))
            return [{"procedure": main_module.TRAVEL_PROCEDURE_NAME}]

        def get_procedure(self, procedure_name: str, **kwargs: object) -> dict[str, object]:
            calls.append(("get", {"procedure_name": procedure_name, **kwargs}))
            return {"procedure": procedure_name, "steps": []}

    class FakeApp:
        memory = FakeMemory()

    monkeypatch.setattr(main_module, "app", FakeApp())

    procedure = main_module._discover_replay_procedure(
        "Flight TA552 to Chicago is cancelled due to weather.",
        "runtime-user",
    )

    assert procedure == {"procedure": main_module.TRAVEL_PROCEDURE_NAME, "steps": []}
    assert calls == [
        (
            "discover",
            {
                "query": "Flight TA552 to Chicago is cancelled due to weather.",
                "user_id": "runtime-user",
                "visibility": "org",
                "similarity_threshold": 0.65,
            },
        ),
        (
            "get",
            {"procedure_name": main_module.TRAVEL_PROCEDURE_NAME, "visibility": "org"},
        ),
    ]


def test_discover_replay_procedure_falls_back_to_canonical_playbook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from travel_agent import main as main_module

    calls: list[tuple[str, dict[str, object]]] = []

    class FakeMemory:
        def discover_procedures(self, **kwargs: object) -> list[dict[str, object]]:
            calls.append(("discover", kwargs))
            return []

        def get_procedure(self, procedure_name: str, **kwargs: object) -> dict[str, object]:
            calls.append(("get", {"procedure_name": procedure_name, **kwargs}))
            return {"procedure": procedure_name, "steps": [{"description": "handler:impact_flow"}]}

    class FakeApp:
        memory = FakeMemory()

    monkeypatch.setattr(main_module, "app", FakeApp())

    procedure = main_module._discover_replay_procedure(
        "Flight TA552 to Chicago is cancelled due to weather.",
        "runtime-user",
    )

    assert procedure == {
        "procedure": main_module.TRAVEL_PROCEDURE_NAME,
        "steps": [{"description": "handler:impact_flow"}],
    }
    assert calls == [
        (
            "discover",
            {
                "query": "Flight TA552 to Chicago is cancelled due to weather.",
                "user_id": "runtime-user",
                "visibility": "org",
                "similarity_threshold": 0.65,
            },
        ),
        (
            "get",
            {"procedure_name": main_module.TRAVEL_PROCEDURE_NAME, "visibility": "org"},
        ),
    ]


def test_supervisor_approval_returns_review_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from travel_agent import main as main_module

    monkeypatch.setenv("PARTNER_COST_THRESHOLD", "650")

    result = main_module.request_supervisor_approval(
        pnr="TRV-10001",
        option_id="ALT-1",
        traveler_name="Avery Stone",
        reason="Partner option exceeds threshold",
        estimated_cost=900,
    )

    payload = json.loads(result)
    suspend_context = payload["suspend_context"]
    presentation = suspend_context["review_presentation"]
    sections = {section["title"]: section for section in presentation["sections"]}
    recovery_fields = {field["key"]: field for field in sections["Recovery option"]["fields"]}
    guidance_fields = {field["key"]: field for field in sections["Review guidance"]["fields"]}

    assert payload["suspend_reason"] == "reaccommodation_approval_required"
    assert suspend_context["allowed_decisions"] == ["approve", "reject"]
    assert suspend_context["review_reasons"] == ["cost exceeds configured review threshold"]
    assert presentation["schema_version"] == "review-presentation/v1"
    assert presentation["title"] == "Travel reaccommodation review"
    assert presentation["summary"] == suspend_context["summary"]
    assert presentation["actions"] == [
        {"id": "approve", "label": "Approve"},
        {"id": "reject", "label": "Reject"},
    ]
    assert recovery_fields["estimated_cost"]["value"] == "$900.00"
    assert guidance_fields["review_reasons"] == {
        "key": "review_reasons",
        "label": "Review reasons",
        "value": ["cost exceeds configured review threshold"],
        "format": "list",
    }
    assert guidance_fields["instructions"]["format"] == "long_text"


def test_supervisor_approval_includes_all_review_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from travel_agent import main as main_module

    monkeypatch.setenv("PARTNER_COST_THRESHOLD", "650")

    result = main_module.request_supervisor_approval(
        pnr="TRV-10001",
        option_id="ALT-1",
        traveler_name="Avery Stone",
        reason="Downgrade on a costly partner option",
        estimated_cost=900,
        downgrade=True,
    )

    payload = json.loads(result)

    assert payload["suspend_context"]["review_reasons"] == [
        "premium traveler downgrade",
        "cost exceeds configured review threshold",
    ]


def test_canonicalize_procedural_extraction_normalizes_invalid_step_types() -> None:
    from travel_agent import main as main_module

    parsed = main_module.ProceduralExtraction(
        procedure=main_module.TRAVEL_PROCEDURE_NAME,
        description="Use for disrupted flights that need passenger recovery.",
        content="Recover the disrupted flight.",
        tags="travel,irrops",
        trigger_conditions=["Cancelled flights"],
        steps=[
            main_module.ProcedureStep(
                step_type="Impact Flow",
                description="Assess disruption | handler:impact_flow",
                content="Assess disruption and prioritize passengers.",
            ),
            main_module.ProcedureStep(
                step_type="Reaccommodation Flow",
                description="Rank recovery | handler:reaccommodation_flow",
                content="Review the passenger and rank compliant recovery options.",
            ),
            main_module.ProcedureStep(
                step_type="Resolution Flow",
                description="Complete recovery | handler:resolution_flow",
                content="Execute the approved recovery and notify the traveler.",
            ),
        ],
    )

    normalized = main_module._canonicalize_procedural_extraction(parsed)

    assert [step["step_type"] for step in normalized["steps"]] == [
        "instruction",
        "instruction",
        "instruction",
    ]
