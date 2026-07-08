import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check-customer-names.sh"
DENYLIST = REPO_ROOT / "scripts" / "customer-names.txt"


def _run() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_passes_with_empty_denylist() -> None:
    original = DENYLIST.read_text()
    try:
        # Ensure only comments/blanks in denylist
        DENYLIST.write_text("# test-only empty denylist\n")
        result = _run()
        assert result.returncode == 0, result.stderr
        assert "nothing to check" in result.stdout
    finally:
        DENYLIST.write_text(original)


def test_fails_when_denied_name_present() -> None:
    original = DENYLIST.read_text()
    leak = REPO_ROOT / "sample_leak_test_fixture.md"
    try:
        DENYLIST.write_text(original + "\nGlobex\n")
        leak.write_text("Globex Corp is our valued customer.\n")
        subprocess.run(["git", "add", "-N", str(leak)], cwd=REPO_ROOT, check=True)
        result = _run()
        assert result.returncode != 0
        assert "Globex" in result.stderr
    finally:
        subprocess.run(
            ["git", "restore", "--staged", str(leak)],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["git", "rm", "-f", "--cached", str(leak)],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
        )
        leak.unlink(missing_ok=True)
        DENYLIST.write_text(original)
