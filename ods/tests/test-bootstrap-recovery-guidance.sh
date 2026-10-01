#!/usr/bin/env bash
# Exercise the existing-install advice as a user would, including a path with spaces.
set -euo pipefail

ods_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

fake_home="$scratch/home"
install="$fake_home/ODS install"
mkdir -p "$install"
printf 'ODS_TEST=1\n' > "$install/.env"
cat > "$install/ods-cli" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
    start) printf 'START_OK\n' ;;
    update) printf 'UPDATE_OK\n' ;;
    *) exit 41 ;;
esac
EOF
chmod +x "$install/ods-cli"

output="$(HOME="$fake_home" ODS_INSTALL_DIR="$install" bash "$ods_root/get-ods.sh" --non-interactive 2>&1)"
start="$(printf '%s\n' "$output" | sed -n 's/^[[:space:]]*To start:[[:space:]]*//p' | head -1)"
[[ -n "$start" ]] || { printf 'Missing start advice:\n%s\n' "$output" >&2; exit 1; }

# The command is emitted by the product. Run it from an unrelated directory to
# prove that both the path and the CLI command work as printed.
result="$(cd / && bash -c "$start")"
[[ "$result" == START_OK ]] || { printf 'Start advice failed: %s\n' "$start" >&2; exit 1; }

update="$(printf '%s\n' "$output" | sed -n 's/^[[:space:]]*To update:[[:space:]]*//p' | head -1)"
[[ -n "$update" ]] || { printf 'Missing update advice:\n%s\n' "$output" >&2; exit 1; }
result="$(cd / && bash -c "$update")"
[[ "$result" == UPDATE_OK ]] || { printf 'Update advice failed: %s\n' "$update" >&2; exit 1; }
printf 'PASS: existing-install recovery advice is runnable\n'
