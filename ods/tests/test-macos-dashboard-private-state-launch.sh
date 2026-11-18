#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pass_count=0
fail_count=0
pass() { pass_count=$((pass_count + 1)); }
fail() { printf 'FAIL: %s\n' "$1" >&2; fail_count=$((fail_count + 1)); }

tmp_dir="$(mktemp -d)"
tmp_dir="$(cd "$tmp_dir" && pwd -P)"
trap 'rm -rf "$tmp_dir"' EXIT

mkdir -p "$tmp_dir/bin" "$tmp_dir/installers/macos/lib" "$tmp_dir/data" "$tmp_dir/volumes"
cp "$ROOT_DIR/installers/macos/lib/dashboard-private-state.py" "$tmp_dir/installers/macos/lib/"
cp "$ROOT_DIR/installers/macos/lib/dashboard-private-state-worker.py" "$tmp_dir/installers/macos/lib/"
cp "$ROOT_DIR/installers/macos/dashboard-private-state.compose.yaml" "$tmp_dir/installers/macos/"
cat << 'COMPOSE' > "$tmp_dir/docker-compose.base.yml"
services:
  dashboard-api:
    image: ods-dashboard-api:latest
COMPOSE

state_file="$tmp_dir/docker_state.txt"
stop_log="$tmp_dir/docker_stops.txt"
volumes_dir="$tmp_dir/volumes"
printf 'running\n' > "$state_file"
: > "$stop_log"

cat > "$tmp_dir/bin/docker" <<EOF
#!/usr/bin/env bash
args=("\$@")
state_file="$state_file"
stop_log="$stop_log"
volumes_dir="$volumes_dir"
install_root="$tmp_dir"

if [[ "\${args[0]:-}" == "context" ]]; then
  if [[ "\${args[1]:-}" == "show" ]]; then
    echo "default"
    exit 0
  elif [[ "\${args[1]:-}" == "inspect" ]]; then
    echo '[{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]'
    exit 0
  fi
elif [[ "\${args[0]:-}" == "info" ]]; then
  echo "test-daemon-id"
  exit 0
elif [[ "\${args[0]:-}" == "compose" ]]; then
  for arg in "\${args[@]}"; do
    if [[ "\$arg" == "config" ]]; then
      python3 -c "import json, sys
data_path = sys.argv[1]
print(json.dumps({
  'name': 'ods',
  'services': {
    'dashboard-api': {
      'image': 'ods-dashboard-api:latest',
      'volumes': [
        {'type': 'bind', 'source': data_path, 'target': '/data'},
        {'type': 'volume', 'source': 'dashboard-pixel-images', 'target': '/data/pixel-images', 'volume': {'nocopy': True}},
        {'type': 'volume', 'source': 'dashboard-pixel-chat-results', 'target': '/data/pixel-chat-results', 'volume': {'nocopy': True}}
      ]
    }
  },
  'volumes': {
    'dashboard-pixel-images': {'name': 'ods_dashboard-pixel-images'},
    'dashboard-pixel-chat-results': {'name': 'ods_dashboard-pixel-chat-results'}
  }
}))" "\$install_root/data"
      exit 0
    elif [[ "\$arg" == "up" ]]; then
      exit 0
    fi
  done
elif [[ "\${args[0]:-}" == "image" && "\${args[1]:-}" == "inspect" ]]; then
  echo '[{"Id": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]'
  exit 0
elif [[ "\${args[0]:-}" == "volume" ]]; then
  if [[ "\${args[1]:-}" == "inspect" ]]; then
    vol_name="\${args[2]}"
    if [[ -f "\$volumes_dir/\$vol_name.json" ]]; then
      echo "[\$(cat "\$volumes_dir/\$vol_name.json")]"
      exit 0
    else
      echo "[]"
      echo "Error response from daemon: get \$vol_name: no such volume" >&2
      exit 1
    fi
  elif [[ "\${args[1]:-}" == "create" ]]; then
    python3 -c "import json, sys
vdir = sys.argv[1]
d_args = sys.argv[2:]
labels = {}
for i, a in enumerate(d_args):
    if a == '--label' and i + 1 < len(d_args):
        k, v = d_args[i+1].split('=', 1)
        labels[k] = v
vol_name = d_args[-1]
vol = {'Name': vol_name, 'Driver': 'local', 'Options': None, 'Labels': labels}
with open(vdir + '/' + vol_name + '.json', 'w') as f:
    json.dump(vol, f)
" "\$volumes_dir" "\${args[@]}"
    exit 0
  fi
elif [[ "\${args[0]:-}" == "ps" ]]; then
  if [[ -f "$tmp_dir/simulate_foreign_consumer.txt" ]]; then
    echo "cid-dashboard-api-123 cid-foreign-456"
  else
    echo "cid-dashboard-api-123"
  fi
  exit 0
elif [[ "\${args[0]:-}" == "inspect" ]]; then
  cid="\${args[1]}"
  if [[ "\$cid" == "cid-foreign-456" ]]; then
    cat << JSON
[
  {
    "Id": "cid-foreign-456",
    "Config": {
      "Labels": {
        "com.docker.compose.project": "other-project",
        "com.docker.compose.service": "dashboard-api"
      }
    },
    "State": {
      "Running": true,
      "Restarting": false
    }
  }
]
JSON
    exit 0
  fi
  state="\$(cat "\$state_file")"
  if [[ "\$state" == "running" ]]; then
    running_val="true"
  else
    running_val="false"
  fi
  cat << JSON
[
  {
    "Id": "cid-dashboard-api-123",
    "Config": {
      "Labels": {
        "com.docker.compose.project": "ods",
        "com.docker.compose.service": "dashboard-api",
        "com.docker.compose.project.working_dir": "\$install_root"
      }
    },
    "State": {
      "Running": \$running_val,
      "Restarting": false
    }
  }
]
JSON
  exit 0
elif [[ "\${args[0]:-}" == "stop" ]]; then
  echo "\${args[1]}" >> "\$stop_log"
  printf 'stopped\n' > "\$state_file"
  echo "\${args[1]}"
  exit 0
elif [[ "\${args[0]:-}" == "run" ]]; then
  exit 0
fi

echo "Unexpected docker command: \$*" >&2
exit 1
EOF
chmod +x "$tmp_dir/bin/docker"
export PATH="$tmp_dir/bin:$PATH"

# Test 1: launch stops running writers under the lock and completes migration + launch
printf 'running\n' > "$state_file"
: > "$stop_log"
output="$(python3 "$tmp_dir/installers/macos/lib/dashboard-private-state.py" \
  launch --operation install --install-dir "$tmp_dir" --flags="-f docker-compose.base.yml" 2>&1)"
exit_code=$?
state="$(cat "$state_file")"
stops="$(cat "$stop_log")"

if [[ $exit_code -eq 0 && "$state" == "stopped" && "$stops" == *"cid-dashboard-api-123"* ]]; then
  pass
else
  fail "launch did not stop running writer before migration (exit=$exit_code, state=$state, stops=$stops, output=$output)"
fi

# Test 2: completed stores start without stopping an already running API
printf 'running\n' > "$state_file"
: > "$stop_log"
started_output="$(python3 "$tmp_dir/installers/macos/lib/dashboard-private-state.py" \
  launch --operation start --install-dir "$tmp_dir" --flags="-f docker-compose.base.yml" 2>&1)"
started_exit=$?
state="$(cat "$state_file")"
stop_count="$(wc -l < "$stop_log" | tr -d ' ')"

if [[ $started_exit -eq 0 && "$state" == "running" && "$stop_count" -eq 0 ]]; then
  pass
else
  fail "launch stopped or failed running API on completed stores (exit=$started_exit, state=$state, stops=$stop_count, output=$started_output)"
fi

# Test 3: unfinished-restore refusal preserves running state without stopping writers
printf '{"path": "dummy", "token": "abc"}\n' > "$tmp_dir/data/.dashboard-private-stores/image-restore-active.json"
printf 'running\n' > "$state_file"
: > "$stop_log"
restore_refuse_output="$(python3 "$tmp_dir/installers/macos/lib/dashboard-private-state.py" \
  launch --operation start --install-dir "$tmp_dir" --flags="-f docker-compose.base.yml" 2>&1 || true)"
state="$(cat "$state_file")"
stop_count="$(wc -l < "$stop_log" | tr -d ' ')"
rm -f "$tmp_dir/data/.dashboard-private-stores/image-restore-active.json"

if echo "$restore_refuse_output" | grep -Fq "private-store-restore-unfinished" && [[ "$state" == "running" && "$stop_count" -eq 0 ]]; then
  pass
else
  fail "unfinished restore did not preserve running state without stops (state=$state, stops=$stop_count, output=$restore_refuse_output)"
fi

# Test 4: foreign consumer validation refuses before issuing any container stop
rm -rf "$tmp_dir/data/.dashboard-private-stores" "$volumes_dir"/*
touch "$tmp_dir/simulate_foreign_consumer.txt"
printf 'running\n' > "$state_file"
: > "$stop_log"
foreign_output="$(python3 "$tmp_dir/installers/macos/lib/dashboard-private-state.py" \
  launch --operation start --install-dir "$tmp_dir" --flags="-f docker-compose.base.yml" 2>&1 || true)"
state="$(cat "$state_file")"
stop_count="$(wc -l < "$stop_log" | tr -d ' ')"
rm -f "$tmp_dir/simulate_foreign_consumer.txt"

if echo "$foreign_output" | grep -Fq "private-store-foreign-consumer" && [[ "$state" == "running" && "$stop_count" -eq 0 ]]; then
  pass
else
  fail "foreign consumer did not refuse without stopping (state=$state, stops=$stop_count, output=$foreign_output)"
fi

# Test 5: explicit prepare without launch still refuses when writers are running
rm -rf "$tmp_dir/data/.dashboard-private-stores" "$volumes_dir"/*
printf 'running\n' > "$state_file"
prepare_output="$(python3 "$tmp_dir/installers/macos/lib/dashboard-private-state.py" \
  prepare --install-dir "$tmp_dir" --flags="-f docker-compose.base.yml" 2>&1 || true)"

if echo "$prepare_output" | grep -Fq "Private dashboard migration/restore needs stopped writers"; then
  pass
else
  fail "prepare without launch did not refuse running writer (output=$prepare_output)"
fi

# Test 6: launch when already stopped completes without needing docker stop
rm -rf "$tmp_dir/data/.dashboard-private-stores" "$volumes_dir"/*
printf 'already_stopped\n' > "$state_file"
stopped_output="$(python3 "$tmp_dir/installers/macos/lib/dashboard-private-state.py" \
  launch --operation install --install-dir "$tmp_dir" --flags="-f docker-compose.base.yml" 2>&1)"
stopped_exit=$?

if [[ $stopped_exit -eq 0 ]]; then
  pass
else
  fail "launch failed when writers were already stopped (exit=$stopped_exit, output=$stopped_output)"
fi

[[ $fail_count -eq 0 ]] || exit 1
