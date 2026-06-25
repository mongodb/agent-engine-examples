#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

source "$ROOT/scripts/dependency-cooldown-policy.env"
UV_VERSION_ERE="${UV_VERSION//./\\.}"
UV_LOCK_SPAN_ERE="${UV_LOCK_SPAN//./\\.}"
PNPM_PACKAGE_MANAGER_ERE="${PNPM_PACKAGE_MANAGER//./\\.}"

fail() {
  echo "dependency cooldown policy: $*" >&2
  exit 1
}

require_file() {
  local file="$1"
  [[ -f "$file" ]] || fail "missing $file"
}

require_pattern() {
  local file="$1"
  local pattern="$2"
  local message="$3"
  grep -Eq "$pattern" "$file" || fail "$message"
}

require_workflow_uv_versions() {
  local file="$1"
  awk -v file="$file" -v expected="$UV_VERSION" '
    function trim(value) {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      return value
    }
    function unquote(value) {
      value = trim(value)
      if (value ~ /^".*"$/) {
        value = substr(value, 2, length(value) - 2)
      }
      return value
    }
    /uses:[[:space:]]*astral-sh\/setup-uv@/ {
      pending_setup_uv = 1
      next
    }
    pending_setup_uv && /^[[:space:]]*version:[[:space:]]*/ {
      value = $0
      sub(/^[[:space:]]*version:[[:space:]]*/, "", value)
      value = unquote(value)
      if (value != expected) {
        printf "%s setup-uv version must be %s, found %s\n", file, expected, value > "/dev/stderr"
        exit 1
      }
      found_setup_uv_version = 1
      pending_setup_uv = 0
      next
    }
    pending_setup_uv && /^[[:space:]]*-[[:space:]]+[A-Za-z_][A-Za-z0-9_-]*:/ {
      printf "%s setup-uv step must pin version %s\n", file, expected > "/dev/stderr"
      exit 1
    }
    END {
      if (pending_setup_uv) {
        printf "%s setup-uv step must pin version %s\n", file, expected > "/dev/stderr"
        exit 1
      }
      if (!found_setup_uv_version) {
        printf "%s must pin at least one setup-uv step to %s\n", file, expected > "/dev/stderr"
        exit 1
      }
    }
  ' "$file" || fail "$file has invalid setup-uv version pins"
}

require_uv_config() {
  local file="$1"
  awk -v file="$file" -v expected="$UV_EXCLUDE_NEWER" '
    function trim(value) {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      return value
    }
    function unquote(value) {
      value = trim(value)
      if (value ~ /^".*"$/) {
        value = substr(value, 2, length(value) - 2)
      }
      return value
    }
    /^[[:space:]]*(#|$)/ { next }
    /^[[:space:]]*\[[^]]+\][[:space:]]*(#.*)?$/ {
      section = $0
      sub(/^[[:space:]]*\[/, "", section)
      sub(/\][[:space:]]*(#.*)?$/, "", section)
      section = trim(section)
      next
    }
    /^[[:space:]]*exclude-newer[[:space:]]*=/ {
      value = $0
      sub(/#.*/, "", value)
      sub(/^[[:space:]]*exclude-newer[[:space:]]*=[[:space:]]*/, "", value)
      value = unquote(value)
      if (section == "") {
        found_top_level = 1
        top_level_value = value
      } else if (section == "pip") {
        found_pip = 1
      }
    }
    END {
      if (!found_top_level || top_level_value != expected) {
        printf "%s must set top-level exclude-newer to \"%s\"\n", file, expected > "/dev/stderr"
        exit 1
      }
      if (found_pip) {
        printf "%s must not set [pip].exclude-newer; use top-level exclude-newer or UV_EXCLUDE_NEWER\n", file > "/dev/stderr"
        exit 1
      }
    }
  ' "$file" || fail "$file has invalid uv cooldown policy"
}

require_pnpm_workspace_config() {
  local file="$1"
  awk -v file="$file" -v expected="$PNPM_MIN_RELEASE_AGE_MINUTES" '
    function trim(value) {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      return value
    }
    function unquote(value) {
      value = trim(value)
      if (value ~ /^["'\''].*["'\'']$/) {
        value = substr(value, 2, length(value) - 2)
      }
      return value
    }
    /^[[:space:]]*(#|$)/ { next }
    {
      line = $0
      sub(/[[:space:]]*#.*/, "", line)
      if (line ~ /^[[:space:]]*packages:[[:space:]]*$/) {
        in_packages = 1
        next
      }
      if (line ~ /^[^[:space:]-][^:]*:/) {
        in_packages = 0
      }
      if (in_packages && line ~ /^[[:space:]]*-[[:space:]]*/) {
        item = line
        sub(/^[[:space:]]*-[[:space:]]*/, "", item)
        if (unquote(item) == ".") {
          found_root_package = 1
        }
      }
      if (line ~ /^minimumReleaseAge[[:space:]]*:/) {
        value = line
        sub(/^minimumReleaseAge[[:space:]]*:[[:space:]]*/, "", value)
        if (trim(value) == expected) {
          found_minimum_age = 1
        }
      }
    }
    END {
      if (!found_root_package) {
        printf "%s must include the root package in packages\n", file > "/dev/stderr"
        exit 1
      }
      if (!found_minimum_age) {
        printf "%s must set minimumReleaseAge: %s\n", file, expected > "/dev/stderr"
        exit 1
      }
    }
  ' "$file" || fail "$file has invalid pnpm cooldown policy"
}

require_locked_uv_syncs() {
  local file="$1"
  awk -v file="$file" '
    function check_command() {
      if (command ~ /(^|[^[:alnum:]_-])uv[[:space:]]+sync([^[:alnum:]_-]|$)/ &&
          command !~ /(^|[[:space:]])--locked([[:space:]]|$)/) {
        printf "%s must run every uv sync with --locked\n", file > "/dev/stderr"
        exit 1
      }
      command = ""
    }
    {
      line = $0
      sub(/[[:space:]]+$/, "", line)
      if (command == "") {
        command = line
      } else {
        command = command " " line
      }
      if (line ~ /\\$/) {
        sub(/[[:space:]]*\\$/, "", command)
        next
      }
      check_command()
    }
    END {
      if (command != "") {
        check_command()
      }
    }
  ' "$file" || fail "$file has an unlocked uv sync"
}

while IFS= read -r pyproject; do
  uv_toml="$(dirname "$pyproject")/uv.toml"
  require_file "$uv_toml"
  require_uv_config "$uv_toml"
done < <(find . -name pyproject.toml \
  -not -path './.git/*' \
  -not -path './.pr-deep-review/*' \
  -not -path '*/.venv/*' \
  -not -path '*/node_modules/*' | sort)

while IFS= read -r uv_lock; do
  require_pattern "$uv_lock" "^[[:space:]]*exclude-newer-span[[:space:]]*=[[:space:]]*\"${UV_LOCK_SPAN_ERE}\"[[:space:]]*$" \
    "$uv_lock must record exclude-newer-span $UV_LOCK_SPAN"
done < <(find . -name uv.lock \
  -not -path './.git/*' \
  -not -path './.pr-deep-review/*' \
  -not -path '*/.venv/*' \
  -not -path '*/node_modules/*' | sort)

while IFS= read -r dockerfile; do
  if grep -Eq '(^|[^[:alnum:]_-])uvx([[:space:]]|$)|(^|[^[:alnum:]_-])uv[[:space:]]+(pip[[:space:]]+install|sync|add|tool[[:space:]]+install|run)' "$dockerfile"; then
    require_pattern "$dockerfile" \
      "^[[:space:]]*ENV[[:space:]]+UV_EXCLUDE_NEWER=\"${UV_EXCLUDE_NEWER}\"[[:space:]]*$" \
      "$dockerfile must set active UV_EXCLUDE_NEWER"
    require_pattern "$dockerfile" "(^[[:space:]]*RUN[[:space:]].*pip[[:space:]]+install.*uv==${UV_VERSION_ERE}([^[:digit:].]|$))|(^[[:space:]]*COPY[[:space:]].*--from=ghcr[.]io/astral-sh/uv:${UV_VERSION_ERE}([^[:digit:].]|$))" \
      "$dockerfile must install uv $UV_VERSION"
    require_locked_uv_syncs "$dockerfile"
  fi
done < <(find . \( -name Dockerfile -o -name 'Dockerfile.*' \) \
  -not -path './.git/*' \
  -not -path './.pr-deep-review/*' \
  -not -path '*/.venv/*' \
  -not -path '*/node_modules/*' | sort)

require_file "templates/chatbot-client/pnpm-workspace.yaml"
require_pnpm_workspace_config "templates/chatbot-client/pnpm-workspace.yaml"
require_pattern "templates/chatbot-client/package.json" \
  "\"packageManager\"[[:space:]]*:[[:space:]]*\"$PNPM_PACKAGE_MANAGER_ERE\"" \
  "templates/chatbot-client/package.json must pin packageManager to $PNPM_PACKAGE_MANAGER"
while IFS= read -r workflow; do
  if grep -Eq 'astral-sh/setup-uv@' "$workflow"; then
    require_workflow_uv_versions "$workflow"
  fi
done < <(find .github/workflows -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)
