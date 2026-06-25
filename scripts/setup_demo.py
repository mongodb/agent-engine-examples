#!/usr/bin/env python3
"""Interactive setup wizard for existing Magenta demo agents."""

from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:
    from ruamel.yaml import YAML
except ModuleNotFoundError:  # pragma: no cover - exercised only on lean systems.
    YAML = None  # type: ignore[assignment]


PROVIDER_ORDER = ("openai", "anthropic", "gemini", "cerebras")
PROVIDER_DEFINITIONS = {
    "openai": ("OpenAI or OpenAI-compatible", "OPENAI_API_KEY", "OPENAI_BASE_URL"),
    "anthropic": ("Anthropic or Anthropic-compatible", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"),
    "gemini": ("Google Gemini", "GEMINI_API_KEY", ""),
    "cerebras": ("Cerebras", "CEREBRAS_API_KEY", ""),
}
PROVIDER_KEY_VARS = {definition[1] for definition in PROVIDER_DEFINITIONS.values()}
PROVIDER_BASE_URL_VARS = {
    definition[2] for definition in PROVIDER_DEFINITIONS.values() if definition[2]
}
REUSABLE_ENV_VARS = (
    PROVIDER_KEY_VARS
    | PROVIDER_BASE_URL_VARS
    | {
        "AZURE_OPENAI_API_VERSION",
        "GUARDRAILS_SERVER_URL",
        "MONGODB_URI",
        "ORG_ID",
        "VOYAGE_API_KEY",
    }
)


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    api_key_var: str
    base_url_var: str = ""


@dataclass(frozen=True)
class EnvAnswers:
    provider: Provider
    provider_api_key: str
    provider_base_url: str
    azure_openai_api_version: str
    enable_memory: bool
    voyage_api_key: str
    guardrails_url: str
    model: str = ""
    playground_port: int | None = None


@dataclass(frozen=True)
class Demo:
    name: str
    path: Path
    summary: str


@dataclass(frozen=True)
class CommandOutcome:
    can_continue: bool
    warning: str | None = None
    version: str = ""


CommandRunner = Callable[[list[str]], tuple[int, str]]


def default_run(command: list[str], cwd: Path | None = None) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return completed.returncode, completed.stdout.strip()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def update_cli_best_effort(run: CommandRunner) -> CommandOutcome:
    update_code, update_output = run(["agentic", "self-update"])
    version_code, version_output = run(["agentic", "version"])
    version = version_output.strip()

    if update_code == 0:
        return CommandOutcome(can_continue=True, version=version)

    warning = "Warning: Could not update the agentic CLI."
    if update_output:
        warning = f"{warning}\n\n{update_output}"
    if version_code == 0 and version:
        warning = f"{warning}\n\nContinuing with your currently installed version:\n{version}"
    else:
        warning = (
            f"{warning}\n\nCould not read the current CLI version. Continuing with "
            "the installed agentic binary."
        )
    return CommandOutcome(can_continue=True, warning=warning, version=version)


def update_repo_best_effort(root: Path) -> None:
    code, output = default_run(["git", "status", "--porcelain"], cwd=root)
    if code != 0:
        print_warning("Could not check git status. Continuing with the current checkout.")
        if output:
            print(output)
        return

    if output.strip():
        print("This checkout has local changes.")
        print("1. Stash changes, pull latest, then pop the stash")
        print("2. Skip updating the repo and continue")
        print("3. Exit")
        choice = prompt_choice("Choose an option", ["1", "2", "3"], default="2")
        if choice == "3":
            raise SystemExit(0)
        if choice == "2":
            print_warning("Skipping repo update. Continuing with the current checkout.")
            return
        pull_code, pull_output = default_run(
            ["git", "pull", "--ff-only", "--autostash"],
            cwd=root,
        )
    else:
        pull_code, pull_output = default_run(["git", "pull", "--ff-only"], cwd=root)

    if pull_code != 0:
        print_warning("Could not pull the latest repo changes. Continuing with this checkout.")
    if pull_output:
        print(pull_output)


def discover_demos(root: Path) -> list[Demo]:
    demos: list[Demo] = []
    for agent_yaml in sorted((root / "agents").glob("*/agent.yaml")):
        demo_dir = agent_yaml.parent
        if not (demo_dir / "env.example").exists():
            print_warning(f"Skipping {demo_dir.name}: env.example is missing.")
            continue
        try:
            data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            print_warning(f"Skipping {demo_dir.name}: agent.yaml could not be parsed.\n{exc}")
            continue
        name = str(data.get("name") or demo_dir.name)
        demos.append(Demo(name=name, path=demo_dir, summary=read_readme_summary(demo_dir)))
    return demos


def read_readme_summary(demo_dir: Path) -> str:
    readme = demo_dir / "README.md"
    if not readme.exists():
        return ""
    lines = readme.read_text(encoding="utf-8").splitlines()
    paragraph: list[str] = []
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#"):
            continue
        paragraph.append(stripped)
    return " ".join(paragraph)


def discover_provider_options(env_example_text: str) -> list[Provider]:
    keys = parse_env_values(env_example_text).keys()
    providers: list[Provider] = []
    for provider_key in PROVIDER_ORDER:
        label, api_key_var, base_url_var = PROVIDER_DEFINITIONS[provider_key]
        if api_key_var in keys:
            providers.append(
                Provider(
                    key=provider_key,
                    label=label,
                    api_key_var=api_key_var,
                    base_url_var=base_url_var if base_url_var in keys else "",
                )
            )
    return providers


def discover_reusable_env_values(root: Path, selected_demo_path: Path) -> dict[str, str]:
    selected_demo_path = selected_demo_path.resolve()
    values: dict[str, str] = {}
    for env_path in sorted((root / "agents").glob("*/.env")):
        if env_path.parent.resolve() == selected_demo_path:
            continue
        for key, value in parse_env_values(env_path.read_text(encoding="utf-8")).items():
            if key in REUSABLE_ENV_VARS and value and key not in values:
                values[key] = value
    return values


def parse_env_values(env_text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in env_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = normalize_env_value(value)
    return values


def normalize_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def render_env_file(
    env_example_text: str,
    answers: EnvAnswers,
    existing_env_text: str = "",
    reusable_env_values: dict[str, str] | None = None,
) -> str:
    reusable_env_values = reusable_env_values or {}
    existing_values = parse_env_values(existing_env_text)
    lines: list[str] = []
    for line in env_example_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            lines.append(line)
            continue

        key, default_value = line.split("=", 1)
        key = key.strip()
        value = existing_values.get(key, reusable_env_values.get(key, default_value))

        if key in PROVIDER_KEY_VARS:
            value = answers.provider_api_key if key == answers.provider.api_key_var else ""
        elif key in PROVIDER_BASE_URL_VARS:
            value = answers.provider_base_url if key == answers.provider.base_url_var else ""
        elif key == "AZURE_OPENAI_API_VERSION":
            value = answers.azure_openai_api_version if answers.provider.key == "openai" else ""
        elif key == "VOYAGE_API_KEY":
            value = answers.voyage_api_key if answers.enable_memory else ""
        elif key == "GUARDRAILS_SERVER_URL":
            value = answers.guardrails_url

        lines.append(f"{key}={value}")
    return "\n".join(lines).rstrip() + "\n"


def apply_agent_yaml_answers(agent_yaml_path: Path, answers: EnvAnswers) -> None:
    data = load_yaml(agent_yaml_path)
    config = data.setdefault("config", {})
    config["provider"] = answers.provider.key
    if answers.model:
        config["model"] = answers.model
    else:
        config.pop("model", None)

    features = data.setdefault("features", {})
    if "memory" in features or answers.enable_memory:
        features["memory"] = answers.enable_memory
    if "guardrails" in features or answers.guardrails_url:
        features["guardrails"] = bool(answers.guardrails_url)

    dump_yaml(agent_yaml_path, data)


def apply_dev_yaml_answers(dev_yaml_path: Path, answers: EnvAnswers) -> None:
    # The playground port is a local-development setting and lives in dev.yaml
    # next to agent.yaml, not in agent.yaml itself.
    if answers.playground_port is None:
        return
    data = load_yaml(dev_yaml_path) if dev_yaml_path.exists() else {}
    services = data.setdefault("services", {})
    playground = services.setdefault("playground", {})
    playground["port"] = answers.playground_port
    dump_yaml(dev_yaml_path, data)


def load_yaml(path: Path) -> Any:
    if YAML is not None:
        yaml_rt = YAML()
        yaml_rt.preserve_quotes = True
        loaded = yaml_rt.load(path.read_text(encoding="utf-8"))
        return loaded if loaded is not None else {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def dump_yaml(path: Path, data: Any) -> None:
    if YAML is not None:
        yaml_rt = YAML()
        yaml_rt.preserve_quotes = True
        yaml_rt.indent(mapping=2, sequence=4, offset=2)
        with path.open("w", encoding="utf-8") as handle:
            yaml_rt.dump(data, handle)
        return
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def collect_answers(demo: Demo, reusable_env_values: dict[str, str] | None = None) -> EnvAnswers:
    reusable_env_values = reusable_env_values or {}
    env_example = (demo.path / "env.example").read_text(encoding="utf-8")
    agent_yaml_data = yaml.safe_load((demo.path / "agent.yaml").read_text(encoding="utf-8")) or {}
    providers = discover_provider_options(env_example)
    if not providers:
        raise SystemExit(
            f"No supported provider API key variables found in {demo.path / 'env.example'}"
        )

    print("\nChoose an LLM provider:")
    for index, provider in enumerate(providers, start=1):
        suffix = " (existing key found)" if reusable_env_values.get(provider.api_key_var) else ""
        print(f"{index}. {provider.label}{suffix}")
    provider_choices = [str(i) for i in range(1, len(providers) + 1)]
    default_provider = next(
        (
            str(index)
            for index, provider in enumerate(providers, start=1)
            if reusable_env_values.get(provider.api_key_var)
        ),
        None,
    )
    provider_choice = prompt_choice("Provider", provider_choices, default=default_provider)
    provider = providers[int(provider_choice) - 1]

    provider_api_key = prompt_secret_with_reusable(provider.api_key_var, reusable_env_values)
    provider_base_url = ""
    if provider.base_url_var:
        provider_base_url = prompt_optional_with_reusable(
            provider.base_url_var,
            reusable_env_values,
            f"{provider.base_url_var} (optional, press Enter for default): ",
        )

    azure_version = ""
    env_keys = parse_env_values(env_example).keys()
    if provider.key == "openai" and "AZURE_OPENAI_API_VERSION" in env_keys:
        azure_version = prompt_optional_with_reusable(
            "AZURE_OPENAI_API_VERSION",
            reusable_env_values,
            "AZURE_OPENAI_API_VERSION (optional): ",
        )

    model = input("Model override (optional, press Enter for demo default): ").strip()

    features = agent_yaml_data.get("features") or {}
    supports_memory = "VOYAGE_API_KEY" in env_keys or "memory" in features
    enable_memory = False
    voyage_api_key = ""
    if supports_memory:
        enable_memory = prompt_yes_no("Enable memory? Requires VOYAGE_API_KEY.", default=False)
        if enable_memory:
            voyage_api_key = prompt_secret_with_reusable("VOYAGE_API_KEY", reusable_env_values)

    guardrails_url = ""
    supports_guardrails = "GUARDRAILS_SERVER_URL" in env_keys or "guardrails" in features
    if supports_guardrails:
        enable_guardrails = prompt_yes_no("Enable guardrails?", default=False)
        if enable_guardrails:
            guardrails_url = prompt_required_with_reusable(
                "GUARDRAILS_SERVER_URL",
                reusable_env_values,
                "GUARDRAILS_SERVER_URL: ",
            )

    dev_yaml_path = demo.path / "dev.yaml"
    dev_yaml_data = (
        yaml.safe_load(dev_yaml_path.read_text(encoding="utf-8")) or {}
        if dev_yaml_path.exists()
        else {}
    )
    current_port = (
        dev_yaml_data.get("services", {}).get("playground", {}).get("port")
        if isinstance(dev_yaml_data.get("services"), dict)
        else 3000
    )
    playground_port = prompt_port(int(current_port or 3000))

    return EnvAnswers(
        provider=provider,
        provider_api_key=provider_api_key,
        provider_base_url=provider_base_url,
        azure_openai_api_version=azure_version,
        enable_memory=enable_memory,
        voyage_api_key=voyage_api_key,
        guardrails_url=guardrails_url,
        model=model,
        playground_port=playground_port,
    )


def write_env(
    demo: Demo,
    answers: EnvAnswers,
    reusable_env_values: dict[str, str] | None = None,
) -> None:
    env_example_path = demo.path / "env.example"
    env_path = demo.path / ".env"
    existing_env = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if env_path.exists():
        overwrite = prompt_yes_no(
            f"{env_path} already exists. Update it with these prompted values?",
            default=True,
        )
        if not overwrite:
            print("Leaving existing .env unchanged.")
            return
    rendered = render_env_file(
        env_example_path.read_text(encoding="utf-8"),
        answers,
        existing_env_text=existing_env,
        reusable_env_values=reusable_env_values,
    )
    env_path.write_text(rendered, encoding="utf-8")
    print(f"Wrote {env_path}")


def prompt_secret(label: str) -> str:
    while True:
        value = getpass.getpass(f"{label}: ").strip()
        if value:
            return value
        print("A value is required.")


def prompt_secret_with_reusable(label: str, reusable_env_values: dict[str, str]) -> str:
    reusable_value = reusable_env_values.get(label, "")
    if reusable_value and prompt_yes_no(f"Use {label} found in another demo .env?", default=True):
        return reusable_value
    return prompt_secret(label)


def prompt_optional_with_reusable(
    label: str,
    reusable_env_values: dict[str, str],
    prompt: str,
) -> str:
    reusable_value = reusable_env_values.get(label, "")
    if reusable_value and prompt_yes_no(f"Use {label} found in another demo .env?", default=True):
        return reusable_value
    return input(prompt).strip()


def prompt_required_with_reusable(
    label: str,
    reusable_env_values: dict[str, str],
    prompt: str,
) -> str:
    reusable_value = prompt_optional_with_reusable(label, reusable_env_values, prompt)
    while not reusable_value:
        print("A value is required.")
        reusable_value = input(prompt).strip()
    return reusable_value


def parse_port_choice(value: str, default: int) -> int | None:
    stripped = value.strip()
    if not stripped:
        return default
    if not stripped.isdigit():
        return None
    port = int(stripped)
    if port < 1 or port > 65535:
        return None
    return port


def prompt_port(default: int) -> int:
    while True:
        port = parse_port_choice(input(f"Playground port [{default}]: "), default)
        if port is not None:
            return port
        print("Enter a port number between 1 and 65535.")


def prompt_choice(prompt: str, choices: list[str], default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if value in choices:
            return value
        print(f"Choose one of: {', '.join(choices)}")


def prompt_yes_no(prompt: str, default: bool) -> bool:
    marker = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{marker}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Enter y or n.")


def print_warning(message: str) -> None:
    print(f"\nWarning: {message}\n")


def choose_demo(demos: list[Demo]) -> Demo:
    print("\nChoose a demo to set up:")
    for index, demo in enumerate(demos, start=1):
        summary = f" - {demo.summary}" if demo.summary else ""
        print(f"{index}. {demo.name}{summary}")
    choice = prompt_choice("Demo", [str(i) for i in range(1, len(demos) + 1)])
    return demos[int(choice) - 1]


def run_dev_up(demo: Demo) -> int:
    print(f"Starting {demo.name} from {demo.path}")
    code = subprocess.call(["agentic", "dev", "up"], cwd=demo.path)
    if code != 0:
        print("\nagentic dev up exited before the local stack was ready.")
        print("Your terminal stays in the directory where you launched this wizard.")
        print("To retry from the selected demo, run:")
        print(f"  cd {demo.path}")
        print("  agentic dev up")
    return int(code)


def main() -> int:
    if len(sys.argv) > 1:
        print("This setup wizard does not take command-line parameters.")
        print("Run: scripts/setup-agent")
        return 2

    root = repo_root()
    print("Magenta demo setup wizard")

    if shutil.which("agentic") is None:
        print("The agentic CLI is required but was not found on PATH.")
        return 1

    cli_result = update_cli_best_effort(lambda command: default_run(command))
    if cli_result.warning:
        print_warning(cli_result.warning)
    elif cli_result.version:
        print(f"agentic CLI is up to date:\n{cli_result.version}")

    update_repo_best_effort(root)

    demos = discover_demos(root)
    if not demos:
        print("No demos with agent.yaml and env.example were found under agents/.")
        return 1

    demo = choose_demo(demos)
    reusable_env_values = discover_reusable_env_values(root, demo.path)
    if reusable_env_values:
        print("Found reusable environment values in other demo .env files.")
    answers = collect_answers(demo, reusable_env_values)
    write_env(demo, answers, reusable_env_values)
    apply_agent_yaml_answers(demo.path / "agent.yaml", answers)
    print(f"Updated {demo.path / 'agent.yaml'}")
    apply_dev_yaml_answers(demo.path / "dev.yaml", answers)
    print(f"Updated {demo.path / 'dev.yaml'}")

    if prompt_yes_no("Start the demo now with agentic dev up?", default=True):
        return run_dev_up(demo)

    port = answers.playground_port or 3000
    print("\nNext:")
    print(f"  cd {demo.path}")
    print("  agentic dev up")
    print(f"  open http://localhost:{port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
