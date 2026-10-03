#!/usr/bin/env bash
# Exercise installer hook selection against a real manifest and a harmless hook.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf "$fixture"' EXIT
fixture_install="$fixture/ODS Install"
extension_dir="$fixture_install/extensions/services/langfuse"
mkdir -p "$fixture_install/lib" "$fixture_install/installers/lib" "$extension_dir/hooks"
cp "$project_dir/lib/service-registry.sh" "$fixture_install/lib/service-registry.sh"
cp "$project_dir/installers/lib/extension-setup-hooks.sh" \
    "$fixture_install/installers/lib/extension-setup-hooks.sh"
cat > "$extension_dir/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: langfuse
  name: Langfuse fixture
  compose_file: compose.yaml
  setup_hook: hooks/post_install.sh
YAML
printf 'services: {langfuse: {image: busybox}}\n' > "$extension_dir/compose.yaml"
cat > "$extension_dir/hooks/post_install.sh" <<'HOOK'
#!/usr/bin/env bash
printf 'ran:%s\n' "$2" >> "$1/hook-runs"
HOOK

log() { :; }
ai_ok() { :; }
ai_warn() { printf '%s\n' "$*" >&2; }
. "$fixture_install/installers/lib/extension-setup-hooks.sh"

# A present manifest and hook do not imply that the extension was selected.
ods_run_selected_extension_setup_hooks "$fixture_install" nvidia "$fixture/install.log" \
    -f docker-compose.base.yml
[[ ! -e "$fixture_install/hook-runs" ]] || {
    echo 'Unselected Langfuse hook ran' >&2
    exit 1
}

# Explicit add-back uses the installed fragment, including a spaced root.
ods_run_selected_extension_setup_hooks "$fixture_install" nvidia "$fixture/install.log" \
    -f docker-compose.base.yml -f extensions/services/langfuse/compose.yaml
[[ "$(cat "$fixture_install/hook-runs")" == 'ran:nvidia' ]] || {
    echo 'Selected Langfuse hook did not run exactly once' >&2
    exit 1
}

# A disabled fragment is not selected even when an old flag is still present.
mv "$extension_dir/compose.yaml" "$extension_dir/compose.yaml.disabled"
ods_run_selected_extension_setup_hooks "$fixture_install" nvidia "$fixture/install.log" \
    -f docker-compose.base.yml -f extensions/services/langfuse/compose.yaml
[[ "$(wc -l < "$fixture_install/hook-runs")" -eq 1 ]] || {
    echo 'Disabled Langfuse hook ran' >&2
    exit 1
}

echo 'Extension hook selection passed'
