#!/usr/bin/env bash
set -euo pipefail

readonly FORBIDDEN_PATTERN="gro""ve|foun""dry|azure-api\\.net|cloud-dev\\.mongo""db\\.com|mongo""db-be\\.glean\\.com|corp\\.mongo""db\\.com|mongo""db\\.slack\\.com|kano""py|ghcr\\.io/10""gen|10""gen/(agentic-platform|magenta-examples)"

if matches=$(git grep -nEi "$FORBIDDEN_PATTERN" -- . ':!*.lock'); then
  printf '%s\n' "Public-reference check found internal references:" >&2
  printf '%s\n' "$matches" >&2
  exit 1
fi
