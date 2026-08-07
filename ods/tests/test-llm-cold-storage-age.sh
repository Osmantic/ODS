#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/scripts/llm-cold-storage.sh"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-cold-age.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

awk '
    /^get_last_access_days\(\) \{/ { capture = 1 }
    capture { print }
    capture && /^}/ { exit }
' "$TARGET" > "$FIXTURE/function.sh"
# shellcheck source=/dev/null
. "$FIXTURE/function.sh"

mkdir -p "$FIXTURE/bin"
cat > "$FIXTURE/bin/uname" <<'EOF'
#!/usr/bin/env bash
echo Linux
EOF
cat > "$FIXTURE/bin/find" <<'EOF'
#!/usr/bin/env bash
echo 100000
EOF
cat > "$FIXTURE/bin/date" <<'EOF'
#!/usr/bin/env bash
echo 272800
EOF
cat > "$FIXTURE/bin/bc" <<'EOF'
#!/usr/bin/env bash
echo "bc must not be called" >&2
exit 99
EOF
chmod +x "$FIXTURE/bin/"*

age="$(PATH="$FIXTURE/bin:$PATH" get_last_access_days "$FIXTURE")"
[[ "$age" == "2" ]] || { echo "FAIL: expected 2 days, got '$age'" >&2; exit 1; }

cat > "$FIXTURE/bin/find" <<'EOF'
#!/usr/bin/env bash
echo 300000
EOF
future_age="$(PATH="$FIXTURE/bin:$PATH" get_last_access_days "$FIXTURE")"
[[ "$future_age" == "0" ]] \
    || { echo "FAIL: future timestamps must clamp to 0 days, got '$future_age'" >&2; exit 1; }

echo "PASS: cold-storage age calculation is dependency-free and bounded"
