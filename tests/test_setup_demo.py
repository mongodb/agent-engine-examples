import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.setup_demo as setup_demo
from scripts.setup_demo import (
    Demo,
    EnvAnswers,
    Provider,
    apply_agent_yaml_answers,
    apply_dev_yaml_answers,
    discover_demos,
    discover_provider_options,
    discover_reusable_env_values,
    parse_env_values,
    parse_port_choice,
    render_env_file,
    run_dev_up,
    update_cli_best_effort,
    update_repo_best_effort,
    write_env,
)


def test_discover_provider_options_follows_env_example() -> None:
    env_example = "\n".join(
        [
            "OPENAI_API_KEY=",
            "OPENAI_BASE_URL=",
            "ANTHROPIC_API_KEY=",
            "GEMINI_API_KEY=",
            "VOYAGE_API_KEY=",
        ]
    )

    options = discover_provider_options(env_example)

    assert [option.key for option in options] == ["openai", "anthropic", "gemini"]


def test_render_env_file_sets_selected_provider_and_memory_key() -> None:
    env_example = "\n".join(
        [
            "ORG_ID=local-dev",
            "OPENAI_API_KEY=",
            "OPENAI_BASE_URL=",
            "AZURE_OPENAI_API_VERSION=",
            "ANTHROPIC_API_KEY=",
            "VOYAGE_API_KEY=",
        ]
    )
    answers = EnvAnswers(
        provider=Provider(
            key="openai",
            label="OpenAI",
            api_key_var="OPENAI_API_KEY",
            base_url_var="OPENAI_BASE_URL",
        ),
        provider_api_key="sk-test",
        provider_base_url="https://llm.example.test",
        azure_openai_api_version="2025-01-01-preview",
        enable_memory=True,
        voyage_api_key="pa-test",
        guardrails_url="",
    )

    rendered = render_env_file(env_example, answers)

    assert "OPENAI_API_KEY=sk-test" in rendered
    assert "OPENAI_BASE_URL=https://llm.example.test" in rendered
    assert "AZURE_OPENAI_API_VERSION=2025-01-01-preview" in rendered
    assert "ANTHROPIC_API_KEY=" in rendered
    assert "VOYAGE_API_KEY=pa-test" in rendered


def test_render_env_file_preserves_existing_before_reusable_defaults() -> None:
    env_example = "\n".join(
        [
            "MONGODB_URI=",
            "ORG_ID=local-dev",
            "OPENAI_API_KEY=",
        ]
    )
    answers = EnvAnswers(
        provider=Provider(
            key="openai",
            label="OpenAI",
            api_key_var="OPENAI_API_KEY",
            base_url_var="OPENAI_BASE_URL",
        ),
        provider_api_key="sk-new",
        provider_base_url="",
        azure_openai_api_version="",
        enable_memory=False,
        voyage_api_key="",
        guardrails_url="",
    )

    rendered = render_env_file(
        env_example,
        answers,
        existing_env_text="MONGODB_URI=mongodb://selected\n",
        reusable_env_values={"MONGODB_URI": "mongodb://other", "ORG_ID": "other-org"},
    )

    assert "MONGODB_URI=mongodb://selected" in rendered
    assert "ORG_ID=other-org" in rendered
    assert "OPENAI_API_KEY=sk-new" in rendered


def test_parse_env_values_strips_whitespace_and_matching_quotes() -> None:
    env_text = "\n".join(
        [
            'OPENAI_API_KEY="sk-existing"',
            "ANTHROPIC_API_KEY='sk-ant'",
            "GEMINI_API_KEY= gemini-key ",
            "EMPTY_VALUE= ",
        ]
    )

    values = parse_env_values(env_text)

    assert values["OPENAI_API_KEY"] == "sk-existing"
    assert values["ANTHROPIC_API_KEY"] == "sk-ant"
    assert values["GEMINI_API_KEY"] == "gemini-key"
    assert values["EMPTY_VALUE"] == ""


def test_discover_reusable_env_values_reads_other_agent_env_files(tmp_path: Path) -> None:
    selected = tmp_path / "agents" / "selected-agent"
    selected.mkdir(parents=True)
    (selected / ".env").write_text("OPENAI_API_KEY=selected-should-not-be-used\n")
    other = tmp_path / "agents" / "other-agent"
    other.mkdir()
    (other / ".env").write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=sk-existing",
                "OPENAI_BASE_URL=https://llm.example.test",
                "VOYAGE_API_KEY=pa-existing",
                "EMPTY_VALUE=",
            ]
        )
        + "\n"
    )

    values = discover_reusable_env_values(tmp_path, selected)

    assert values == {
        "OPENAI_API_KEY": "sk-existing",
        "OPENAI_BASE_URL": "https://llm.example.test",
        "VOYAGE_API_KEY": "pa-existing",
    }


def test_parse_port_choice_rejects_invalid_or_out_of_range_values() -> None:
    assert parse_port_choice("", 3000) == 3000
    assert parse_port_choice("3010", 3000) == 3010
    assert parse_port_choice("abc", 3000) is None
    assert parse_port_choice("0", 3000) is None
    assert parse_port_choice("70000", 3000) is None


def test_discover_demos_skips_malformed_agent_yaml(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    good = agents_dir / "good-agent"
    good.mkdir(parents=True)
    (good / "agent.yaml").write_text("name: good-agent\n", encoding="utf-8")
    (good / "env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    malformed = agents_dir / "bad-agent"
    malformed.mkdir()
    (malformed / "agent.yaml").write_text("name: [unterminated\n", encoding="utf-8")
    (malformed / "env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")

    demos = discover_demos(tmp_path)

    assert [demo.name for demo in demos] == ["good-agent"]


def test_apply_agent_yaml_answers_updates_config_and_features(tmp_path: Path) -> None:
    agent_yaml = tmp_path / "agent.yaml"
    agent_yaml.write_text(
        "\n".join(
            [
                "name: demo-agent",
                "entrypoint: demo_agent.main:app",
                "features:",
                "  memory: false",
                "  guardrails: false",
            ]
        )
        + "\n"
    )
    answers = EnvAnswers(
        provider=Provider(
            key="anthropic",
            label="Anthropic",
            api_key_var="ANTHROPIC_API_KEY",
            base_url_var="ANTHROPIC_BASE_URL",
        ),
        provider_api_key="",
        provider_base_url="",
        azure_openai_api_version="",
        enable_memory=True,
        voyage_api_key="",
        guardrails_url="https://guardrails.example.test",
        model="claude-sonnet-4-5",
        playground_port=3010,
    )

    apply_agent_yaml_answers(agent_yaml, answers)

    data = yaml.safe_load(agent_yaml.read_text())
    assert data["config"] == {"provider": "anthropic", "model": "claude-sonnet-4-5"}
    assert data["features"] == {"memory": True, "guardrails": True}
    # The playground port is a local-dev setting and must not be written to agent.yaml.
    assert "services" not in data


def test_apply_dev_yaml_answers_writes_playground_port(tmp_path: Path) -> None:
    dev_yaml = tmp_path / "dev.yaml"
    dev_yaml.write_text("services:\n  playground:\n    port: 3000\n")
    answers = EnvAnswers(
        provider=Provider(
            key="anthropic",
            label="Anthropic",
            api_key_var="ANTHROPIC_API_KEY",
            base_url_var="ANTHROPIC_BASE_URL",
        ),
        provider_api_key="",
        provider_base_url="",
        azure_openai_api_version="",
        enable_memory=False,
        voyage_api_key="",
        guardrails_url="",
        model="",
        playground_port=3010,
    )

    apply_dev_yaml_answers(dev_yaml, answers)

    data = yaml.safe_load(dev_yaml.read_text())
    assert data["services"]["playground"]["port"] == 3010


def test_apply_dev_yaml_answers_creates_file_when_missing(tmp_path: Path) -> None:
    dev_yaml = tmp_path / "dev.yaml"
    answers = EnvAnswers(
        provider=Provider(
            key="anthropic",
            label="Anthropic",
            api_key_var="ANTHROPIC_API_KEY",
            base_url_var="ANTHROPIC_BASE_URL",
        ),
        provider_api_key="",
        provider_base_url="",
        azure_openai_api_version="",
        enable_memory=False,
        voyage_api_key="",
        guardrails_url="",
        model="",
        playground_port=3010,
    )

    apply_dev_yaml_answers(dev_yaml, answers)

    assert dev_yaml.exists()
    data = yaml.safe_load(dev_yaml.read_text())
    assert data["services"]["playground"]["port"] == 3010


def test_update_cli_best_effort_warns_and_continues_on_failure() -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> tuple[int, str]:
        commands.append(command)
        if command == ["agentic", "self-update"]:
            return 1, "network unavailable"
        if command == ["agentic", "version"]:
            return 0, "0.1.33-alpha"
        raise AssertionError(command)

    result = update_cli_best_effort(fake_run)

    assert result.can_continue is True
    assert result.warning is not None
    assert "Could not update" in result.warning
    assert result.version == "0.1.33-alpha"
    assert commands == [["agentic", "self-update"], ["agentic", "version"]]


def test_update_repo_best_effort_skips_pull_when_dirty_user_skips(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], cwd: Path | None = None) -> tuple[int, str]:
        commands.append(command)
        return 0, " M README.md" if command == ["git", "status", "--porcelain"] else ""

    monkeypatch.setattr(setup_demo, "default_run", fake_run)
    monkeypatch.setattr(setup_demo, "prompt_choice", lambda *args, **kwargs: "2")

    update_repo_best_effort(tmp_path)

    assert commands == [["git", "status", "--porcelain"]]


def test_write_env_creates_env_from_template(tmp_path: Path) -> None:
    demo_path = tmp_path / "agent"
    demo_path.mkdir()
    (demo_path / "env.example").write_text("OPENAI_API_KEY=\nORG_ID=local-dev\n", encoding="utf-8")
    demo = Demo(name="agent", path=demo_path, summary="")
    answers = EnvAnswers(
        provider=Provider(
            key="openai",
            label="OpenAI",
            api_key_var="OPENAI_API_KEY",
            base_url_var="OPENAI_BASE_URL",
        ),
        provider_api_key="sk-new",
        provider_base_url="",
        azure_openai_api_version="",
        enable_memory=False,
        voyage_api_key="",
        guardrails_url="",
    )

    write_env(demo, answers, reusable_env_values={"ORG_ID": "reused-org"})

    assert (demo_path / ".env").read_text(encoding="utf-8") == (
        "OPENAI_API_KEY=sk-new\nORG_ID=reused-org\n"
    )


def test_run_dev_up_prints_retry_command_when_agentic_fails(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    demo = Demo(name="agent", path=tmp_path / "agents" / "agent", summary="")
    calls: list[tuple[list[str], Path]] = []

    def fake_call(command: list[str], cwd: Path) -> int:
        calls.append((command, cwd))
        return 1

    monkeypatch.setattr(setup_demo.subprocess, "call", fake_call)

    code = run_dev_up(demo)

    output = capsys.readouterr().out
    assert code == 1
    assert calls == [(["agentic", "dev", "up"], demo.path)]
    assert f"cd {demo.path}" in output
    assert "agentic dev up" in output
