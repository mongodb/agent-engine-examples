# Customer-Name Validation Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a validation check that fails CI (and local CI) when any prohibited customer name appears in the examples repo.

**Architecture:** A denylist file (`scripts/customer-names.txt`) is the single source of truth for banned names. A pure bash+git script (`scripts/check-customer-names.sh`) enumerates tracked text files and greps them for each denied name (case-insensitive, whole-word), failing with `file:line:name` output on any hit. The check is wired into `scripts/local-ci.sh` and a new lightweight `.github/workflows/ci.yml` job. A pytest wrapper exercises the script end-to-end.

**Tech Stack:** Bash, git, grep; pytest (subprocess) for the test; GitHub Actions.

## Global Constraints

- Scripts use `set -euo pipefail`, resolve `ROOT` via `BASH_SOURCE`, and provide a `fail()` helper that prints to stderr and exits non-zero — match `scripts/check-uv-locks.sh` and `scripts/lint-dependency-cooldowns.sh` exactly.
- The check must require no Python/uv dependencies — pure bash + git + grep only.
- Matching is case-insensitive, whole-word.
- An empty denylist (no non-comment entries) must pass (exit 0).
- The denylist file itself, `*.lock` files, and binary files must be excluded from scanning.

---

### Task 1: Denylist file + check script

**Files:**
- Create: `scripts/customer-names.txt`
- Create: `scripts/check-customer-names.sh` (chmod +x)

**Interfaces:**
- Produces: executable `scripts/check-customer-names.sh` that exits 0 when clean/empty-list, exits 1 and prints `file:line:matched-name` to stderr when a denied name is found. Reads names from `scripts/customer-names.txt` (one per line; blank lines and `#`-prefixed lines ignored).

- [ ] **Step 1: Create the denylist file**

`scripts/customer-names.txt`:

```
# Prohibited customer names — one per line.
# Blank lines and lines starting with '#' are ignored.
# Matching is case-insensitive and whole-word.
# Add real customer/company names here to prevent them leaking into examples.
```

- [ ] **Step 2: Write the check script**

`scripts/check-customer-names.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DENYLIST="$ROOT/scripts/customer-names.txt"

fail() {
  echo "customer name check: $*" >&2
  exit 1
}

[[ -f "$DENYLIST" ]] || fail "missing $DENYLIST"

# Load denied names, skipping blanks and comments; trim surrounding whitespace.
names=()
while IFS= read -r line; do
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [[ -z "$line" || "$line" == \#* ]] && continue
  names+=("$line")
done < "$DENYLIST"

if ((${#names[@]} == 0)); then
  echo "customer name check: denylist is empty, nothing to check"
  exit 0
fi

# Build an alternation ERE of whole-word, escaped names.
escaped=()
for name in "${names[@]}"; do
  escaped+=("$(printf '%s' "$name" | sed -E 's/[][(){}.^$*+?|\\]/\\&/g')")
done
IFS='|' read -r pattern <<< "$(printf '%s|' "${escaped[@]}")"
pattern="${pattern%|}"

# Enumerate tracked files, excluding the denylist itself and lockfiles.
mapfile -t files < <(git ls-files \
  | grep -v -e '^scripts/customer-names.txt$' -e '\.lock$' -e '^uv\.lock$')

((${#files[@]})) || { echo "customer name check: no files to scan"; exit 0; }

# grep -I skips binary files; -inw = case-insensitive, whole-word, line numbers.
if matches="$(grep -Inw -E -i "$pattern" "${files[@]}")"; then
  echo "Prohibited customer name(s) found:" >&2
  echo "$matches" >&2
  fail "remove customer names or update scripts/customer-names.txt if legitimate"
fi

echo "customer name check: no prohibited names found"
```

- [ ] **Step 3: Make it executable**

Run: `chmod +x scripts/check-customer-names.sh`

- [ ] **Step 4: Run against clean repo (empty denylist)**

Run: `scripts/check-customer-names.sh`
Expected: prints "denylist is empty, nothing to check", exit 0.

- [ ] **Step 5: Manually verify a hit**

Run: `printf 'Globex\n' >> scripts/customer-names.txt && echo "Globex Corp is our client" > /tmp/leak.txt && git add -N /tmp/leak.txt 2>/dev/null; scripts/check-customer-names.sh; echo "exit=$?"`
Expected: since `/tmp/leak.txt` isn't tracked it won't be scanned — instead add a temp tracked file: `echo "Globex demo" > sample_leak.md && git add sample_leak.md && scripts/check-customer-names.sh || echo "correctly failed"`. Then clean up: `git rm -f sample_leak.md && git checkout scripts/customer-names.txt`.
Expected: script prints `sample_leak.md:1:Globex demo` and fails.

- [ ] **Step 6: Commit**

```bash
git add scripts/customer-names.txt scripts/check-customer-names.sh
git commit -m "AP-3401: add customer-name denylist and check script"
```

---

### Task 2: pytest wrapper

**Files:**
- Create: `tests/test_check_customer_names.py`

**Interfaces:**
- Consumes: `scripts/check-customer-names.sh` from Task 1.

- [ ] **Step 1: Write the failing test**

`tests/test_check_customer_names.py`:

```python
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
    result = _run()
    assert result.returncode == 0, result.stderr


def test_fails_when_denied_name_present(tmp_path, monkeypatch) -> None:
    original = DENYLIST.read_text()
    leak = REPO_ROOT / "sample_leak_test_fixture.md"
    try:
        DENYLIST.write_text(original + "\nGlobex\n")
        leak.write_text("Globex is our valued customer.\n")
        subprocess.run(["git", "add", str(leak)], cwd=REPO_ROOT, check=True)
        result = _run()
        assert result.returncode != 0
        assert "Globex" in result.stderr
    finally:
        subprocess.run(["git", "rm", "-f", "--quiet", str(leak)],
                       cwd=REPO_ROOT, check=False)
        leak.unlink(missing_ok=True)
        DENYLIST.write_text(original)
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/test_check_customer_names.py -v`
Expected: both tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_check_customer_names.py
git commit -m "AP-3401: add tests for customer-name check"
```

---

### Task 3: Wire into local + CI

**Files:**
- Modify: `scripts/local-ci.sh` (near the top, after `uv sync`, alongside ruff checks)
- Modify: `.github/workflows/ci.yml` (new `customer-names` job + `ci-success` needs/checks)

**Interfaces:**
- Consumes: `scripts/check-customer-names.sh` from Task 1.

- [ ] **Step 1: Add step to local-ci.sh**

In `scripts/local-ci.sh`, immediately after the `uv sync --group dev` line and before `uv run ruff check .`, add:

```bash
scripts/check-customer-names.sh
```

- [ ] **Step 2: Add CI job to ci.yml**

Add this job (after `pr-title-check`, before `lint`):

```yaml
  customer-names:
    name: Customer Name Check
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          fetch-depth: 0
      - name: Check for customer names
        run: scripts/check-customer-names.sh
```

- [ ] **Step 3: Add to ci-success gate**

In the `ci-success` job, add `customer-names` to `needs`:

```yaml
    needs: [pr-title-check, customer-names, lint, type-check, example-smoke]
```

And add to the failure check condition (inside the `if` block, as a new `||` clause):

```bash
             [[ "${{ needs.customer-names.result }}" != "success" ]] || \
```

- [ ] **Step 4: Verify locally**

Run: `bash scripts/check-customer-names.sh && echo "local check OK"`
Expected: exit 0, "local check OK".

Optionally validate the workflow YAML: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "yaml valid"`

- [ ] **Step 5: Commit**

```bash
git add scripts/local-ci.sh .github/workflows/ci.yml
git commit -m "AP-3401: wire customer-name check into local and CI"
```

---

## Notes for the implementer

- `git ls-files` output is relative to repo root; the script `cd`s to `ROOT` first so paths and `grep` targets line up.
- The `sed` escaping in Task 1 handles regex metacharacters in names so a name like `A.B Corp` is matched literally.
- If `fetch-depth: 0` proves unnecessary (the script only reads the working tree, not history), it can be dropped — but leaving it is harmless and future-proofs history-based extensions.
