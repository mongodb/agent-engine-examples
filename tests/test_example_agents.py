from __future__ import annotations

from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / "agents"
ALL_AGENT_DIRS = [
    AGENTS_DIR / "simple-agent-example",
    AGENTS_DIR / "recruiting-assistant-agent",
    AGENTS_DIR / "insurance-agent",
]
IMPORTED_AGENT_DIRS = [
    AGENTS_DIR / "recruiting-assistant-agent",
    AGENTS_DIR / "insurance-agent",
]


class ExampleAgentTests(TestCase):
    def test_requested_example_agents_are_present(self) -> None:
        missing = [path.name for path in IMPORTED_AGENT_DIRS if not path.is_dir()]

        self.assertEqual(missing, [])

    def test_agents_only_live_under_agents_directory(self) -> None:
        root_agent_dirs = [ROOT / path.name for path in ALL_AGENT_DIRS]
        unexpected = [path.name for path in root_agent_dirs if path.exists()]

        self.assertEqual(unexpected, [])

    def test_root_readme_lists_requested_example_agents(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        missing = [
            str(path.relative_to(ROOT))
            for path in ALL_AGENT_DIRS
            if str(path.relative_to(ROOT)) not in readme
        ]

        self.assertEqual(missing, [])

    def test_examples_do_not_reference_legacy_brand(self) -> None:
        scanned_suffixes = {
            ".dockerignore",
            ".env",
            ".example",
            ".md",
            ".py",
            ".toml",
            ".txt",
            ".yaml",
            ".yml",
        }
        offenders: list[str] = []

        for path in [ROOT / "README.md", *IMPORTED_AGENT_DIRS]:
            if path.is_file():
                files = [path]
            elif path.exists():
                files = [
                    candidate
                    for candidate in path.rglob("*")
                    if candidate.is_file() and candidate.suffix in scanned_suffixes
                ]
            else:
                continue

            for candidate in files:
                legacy_brand = "ma" + "genta"
                if legacy_brand in candidate.read_text(encoding="utf-8").lower():
                    offenders.append(str(candidate.relative_to(ROOT)))

        self.assertEqual(offenders, [])

    def test_examples_use_current_repo_name(self) -> None:
        recruiting_readme = (AGENTS_DIR / "recruiting-assistant-agent" / "README.md").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("atlasap-examples", recruiting_readme)

    def test_recruiting_assistant_avoids_source_company_branding(self) -> None:
        scanned_suffixes = {".md", ".py", ".toml", ".yaml", ".yml"}
        offenders: list[str] = []

        for candidate in (AGENTS_DIR / "recruiting-assistant-agent").rglob("*"):
            if not candidate.is_file() or candidate.suffix not in scanned_suffixes:
                continue
            if "Indeed" in candidate.read_text(encoding="utf-8"):
                offenders.append(str(candidate.relative_to(ROOT)))

        self.assertEqual(offenders, [])
