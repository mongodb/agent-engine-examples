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

# Build an alternation ERE pattern of whole-word escaped names.
escaped=()
for name in "${names[@]}"; do
  escaped+=("$(printf '%s' "$name" | sed -E 's/[][(){}.^$*+?|\\]/\\&/g')")
done
pattern="$(printf '%s|' "${escaped[@]}")"
pattern="${pattern%|}"

# Enumerate tracked files into a temp file, excluding the denylist and lockfiles.
tmpfile="$(mktemp)"
trap 'rm -f "$tmpfile"' EXIT
git ls-files \
  | grep -v -e '^scripts/customer-names\.txt$' -e '\.lock$' -e '^uv\.lock$' \
  > "$tmpfile"

if [[ ! -s "$tmpfile" ]]; then
  echo "customer name check: no files to scan"
  exit 0
fi

# grep -I skips binary files; -inw = case-insensitive, whole-word, line numbers.
# xargs passes the file list to grep without hitting arg limits.
if matches="$(xargs grep -Inw -E "$pattern" < "$tmpfile" 2>/dev/null)"; then
  echo "Prohibited customer name(s) found:" >&2
  echo "$matches" >&2
  fail "remove customer names from the files above, or update scripts/customer-names.txt if the use is legitimate"
fi

echo "customer name check: no prohibited names found"
