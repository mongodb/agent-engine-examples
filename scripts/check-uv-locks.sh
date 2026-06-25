#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

source "$ROOT/scripts/dependency-cooldown-policy.env"
UV=(uvx --from "uv==${UV_VERSION}" uv)

fail() {
  echo "uv lock check: $*" >&2
  exit 1
}

cleanup_dirs=()
cleanup() {
  if ((${#cleanup_dirs[@]})); then
    rm -rf "${cleanup_dirs[@]}"
  fi
}
trap cleanup EXIT

echo "Checking root uv.lock"
"${UV[@]}" lock --check

while IFS= read -r lockfile; do
  project_dir="$(dirname "$lockfile")"
  temp_dir="$(mktemp -d)"
  cleanup_dirs+=("$temp_dir")

  rsync -a \
    --exclude .venv \
    --exclude .pytest_cache \
    --exclude __pycache__ \
    "$project_dir/" "$temp_dir/project/"

  echo "Checking $lockfile"
  "${UV[@]}" lock --check \
    --directory "$temp_dir/project"
done < <(find . -name uv.lock \
  -not -path './uv.lock' \
  -not -path './.git/*' \
  -not -path './.pr-deep-review/*' \
  -not -path '*/.venv/*' \
  -not -path '*/node_modules/*' | sort)

PYTHON_3_11="$("${UV[@]}" python find 3.11)" || fail "Python 3.11 is required for lockfile metadata checks"

"$PYTHON_3_11" - <<'PY'
import pathlib
import re
import sys
import tomllib

root_lock = tomllib.loads(pathlib.Path("uv.lock").read_text())
root_packages = {package["name"]: package for package in root_lock["package"]}

checked_projects = sorted(
    {
        dockerfile.parent
        for dockerfile in pathlib.Path("agents").glob("*/Dockerfile*")
        if (dockerfile.parent / "uv.lock").exists()
    }
)


def package_identity(package):
    source = package.get("source") or {}
    return package.get("version"), tuple(sorted(source.items()))

failed = False
for project in checked_projects:
    pyproject = tomllib.loads((project / "pyproject.toml").read_text())
    lock = tomllib.loads((project / "uv.lock").read_text())
    lock_packages = {package["name"]: package for package in lock["package"]}
    for dependency in pyproject["project"]["dependencies"]:
        name = re.split(r"[<>=!~\[ ;]", dependency, maxsplit=1)[0]
        name = name.lower().replace("_", "-")
        root_package = root_packages.get(name)
        lock_package = lock_packages.get(name)
        if root_package and lock_package and package_identity(root_package) != package_identity(lock_package):
            print(
                f"{project}/uv.lock resolves {name} as {package_identity(lock_package)}, "
                f"but root uv.lock resolves it as {package_identity(root_package)}",
                file=sys.stderr,
            )
            failed = True

if failed:
    sys.exit(1)
PY
