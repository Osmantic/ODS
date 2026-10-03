#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "provision-fixture.sh must run through sudo" >&2; exit 1; }
[[ $# == 3 && $1 == install && $3 == --confirm ]] || { echo "Usage: provision-fixture.sh install EXPECTED_HOSTNAME --confirm" >&2; exit 2; }
expected=$2
[[ "$expected" =~ ^[A-Za-z0-9._-]{1,255}$ ]] || { echo "Unsafe expected hostname" >&2; exit 2; }
observed=$(hostname | tr -d '\r')
[[ "$observed" == "$expected" ]] || { echo "Fixture target mismatch: expected $expected, observed $observed" >&2; exit 1; }
id pixel-runner >/dev/null 2>&1 || { echo "pixel-runner must be enrolled first" >&2; exit 1; }

install -d -o root -g root -m 0755 \
  /opt/pixel-ops-fixture /opt/pixel-ops-fixture/releases \
  /opt/pixel-ops-fixture/releases/v1/bin \
  /opt/pixel-ops-fixture/releases/candidate-20260805/bin \
  /opt/pixel-ops-fixture/releases/bad-20260805/bin \
  /opt/pixel-ops-fixture/installed
temporary=$(mktemp -d /tmp/pixel-ops-fixture.XXXXXX)
trap 'rm -rf -- "$temporary"' EXIT
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$temporary/healthy"
printf '%s\n' '#!/usr/bin/env bash' 'exit 1' > "$temporary/unhealthy"
install -o root -g root -m 0755 "$temporary/healthy" /opt/pixel-ops-fixture/releases/v1/bin/healthcheck
install -o root -g root -m 0755 "$temporary/healthy" /opt/pixel-ops-fixture/releases/candidate-20260805/bin/healthcheck
install -o root -g root -m 0755 "$temporary/unhealthy" /opt/pixel-ops-fixture/releases/bad-20260805/bin/healthcheck
ln -sfn /opt/pixel-ops-fixture/releases/v1 /opt/pixel-ops-fixture/.current-new
mv -Tf /opt/pixel-ops-fixture/.current-new /opt/pixel-ops-fixture/current
rm -f -- /opt/pixel-ops-fixture/previous

cat > "$temporary/pixel-ops-fixture.service" <<'EOF'
[Unit]
Description=Disposable Pixel Operations acceptance fixture

[Service]
Type=simple
ExecStart=/usr/bin/sleep infinity
Restart=no
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
install -o root -g root -m 0644 "$temporary/pixel-ops-fixture.service" /etc/systemd/system/pixel-ops-fixture.service
printf '%s\n' 'Pixel Operations disposable acceptance log' > /var/log/pixel-ops-fixture.log
chown root:root /var/log/pixel-ops-fixture.log
chmod 0644 /var/log/pixel-ops-fixture.log

install -d -o pixel-runner -g pixel-runner -m 0750 \
  /var/lib/pixel-runner/jobs/client-app /var/lib/pixel-runner/jobs/artifacts
if [[ ! -d /var/lib/pixel-runner/jobs/client-app/.git ]]; then
  sudo -u pixel-runner git -C /var/lib/pixel-runner/jobs/client-app init -q -b main
  printf '%s\n' '# Disposable Pixel Operations fixture' > /var/lib/pixel-runner/jobs/client-app/README.md
  chown pixel-runner:pixel-runner /var/lib/pixel-runner/jobs/client-app/README.md
  sudo -u pixel-runner git -C /var/lib/pixel-runner/jobs/client-app add README.md
  sudo -u pixel-runner git -C /var/lib/pixel-runner/jobs/client-app \
    -c user.name=pixel-fixture -c user.email=pixel-fixture.invalid commit -q -m fixture
fi
if [[ ! -d /opt/pixel-ops-fixture/origin.git ]]; then
  sudo -u pixel-runner git clone -q --bare /var/lib/pixel-runner/jobs/client-app /tmp/pixel-ops-fixture-origin.git
  mv /tmp/pixel-ops-fixture-origin.git /opt/pixel-ops-fixture/origin.git
  chown -R pixel-runner:pixel-runner /opt/pixel-ops-fixture/origin.git
  chmod -R u+rwX,g-rwx,o-rwx /opt/pixel-ops-fixture/origin.git
fi
if ! sudo -u pixel-runner git -C /var/lib/pixel-runner/jobs/client-app remote get-url origin >/dev/null 2>&1; then
  sudo -u pixel-runner git -C /var/lib/pixel-runner/jobs/client-app remote add origin /opt/pixel-ops-fixture/origin.git
fi
printf '%s\n' 'pixel-ops-disposable-package-v1' > /var/lib/pixel-runner/jobs/artifacts/pixel-fixture.pkg
chown pixel-runner:pixel-runner /var/lib/pixel-runner/jobs/artifacts/pixel-fixture.pkg
chmod 0600 /var/lib/pixel-runner/jobs/artifacts/pixel-fixture.pkg
systemctl daemon-reload
systemctl enable --now pixel-ops-fixture.service >/dev/null
package_hash=$(sha256sum /var/lib/pixel-runner/jobs/artifacts/pixel-fixture.pkg | awk '{print $1}')
printf '{"schemaVersion":1,"fixture":"installed","hostname":"%s","candidateRelease":"candidate-20260805","badRelease":"bad-20260805","packagePath":"/var/lib/pixel-runner/jobs/artifacts/pixel-fixture.pkg","packageSha256":"%s"}\n' "$observed" "$package_hash"
