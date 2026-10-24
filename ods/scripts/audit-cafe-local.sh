#!/usr/bin/env bash
# Read-only inventory of local Cafe/ODS work. Does not read or print .env values,
# does not start a server, and does not modify repositories or running services.
set -u

roots=(
  "$HOME/ods-git"
  "$HOME/ods-cafe-pr"
  "$HOME/leones-cafe-staging"
  "$HOME/ods"
)

printf '\n== Cafe/ODS local audit ==\n'
printf 'Date: '; date -Is
printf 'Host: '; hostname
printf '\n== Worktree status ==\n'

for root in "${roots[@]}"; do
  [ -d "$root" ] || continue
  printf '\n-- %s --\n' "$root"
  if git -C "$root" rev-parse --show-toplevel >/dev/null 2>&1; then
    printf 'top: '; git -C "$root" rev-parse --show-toplevel
    printf 'branch: '; git -C "$root" branch --show-current
    printf 'HEAD: '; git -C "$root" rev-parse --short HEAD
    git -C "$root" status --short
    git -C "$root" log -5 --oneline --decorate
  else
    printf 'not a Git worktree\n'
  fi
done

printf '\n== Existing Cafe/ICD source files ==\n'
for root in "${roots[@]}"; do
  [ -d "$root" ] || continue
  find "$root" \
    -type d \( -name .git -o -name node_modules -o -name data -o -name models -o -name .venv \) -prune -o \
    -type f \( -iname '*cafe*' -o -iname '*inference*configuration*' -o -iname '*activation*overlay*' -o -iname '*switchboard*overlay*' \) -print 2>/dev/null
done | sort -u

printf '\n== Parameter references in source (not .env) ==\n'
pattern='host-moe|cpu-moe|ssd-streaming|ngram-ssd|turbo[234]|spec-draft-n-max|spec-type|N_GPU_LAYERS|CTX_SIZE|LLAMA_BATCH_SIZE|CACHE_TYPE_[KV]|FLASH_ATTN|ODS_INFERENCE_RUNTIME|pipeline-parallel|safetensors-native|safetensors-outtype|override-tensor'
for root in "${roots[@]}"; do
  [ -d "$root" ] || continue
  printf '\n-- %s --\n' "$root"
  grep -RInE --binary-files=without-match \
    --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=data \
    --exclude-dir=models --exclude-dir=.venv --exclude='.env' --exclude='*.lock' \
    "$pattern" "$root" 2>/dev/null | head -n 160 || true
done

printf '\n== Installed executable probes ==\n'
for root in "${roots[@]}"; do
  [ -d "$root" ] || continue
  while IFS= read -r bin; do
    [ -x "$bin" ] || continue
    printf '\n-- %s --\n' "$bin"
    "$bin" --version 2>&1 | head -n 3 || true
    "$bin" --help 2>&1 | grep -E -- "$pattern" | head -n 100 || true
  done < <(find "$root" -type d \( -name .git -o -name node_modules -o -name data -o -name models -o -name .venv \) -prune -o -type f \( -name llama-server -o -name llama-cli -o -name llama-completion \) -perm -111 -print 2>/dev/null)
done

printf '\n== End of read-only audit ==\n'
printf 'No service was started, no environment value was printed, and no file was changed.\n'
