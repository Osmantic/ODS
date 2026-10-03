# Limb-kit live isolation gate

`run-live.sh` is a destructive lifecycle qualification that is intentionally limited to
a disposable rootful Docker container running systemd. It generates a synthetic pack and
publisher, installs and enables the real worker, verifies timer health and projection
contents, proves a separate gateway account can read the projection only while enabled,
then disables and removes the pack and checks for filesystem, unit, account, and group
residue.

The harness refuses to run unless all of these are true:

- `PIXEL_LIMB_LIVE_CONFIRM=disposable-systemd-container` is set;
- `/.dockerenv` exists and `systemd-detect-virt --container` reports a container;
- the caller is root inside that container;
- no exact `live-probe` target already exists.

Install Python, OpenSSH client, systemd, and ACL tooling in the disposable container,
copy `scripts/limb-kit.py` to `/usr/local/bin/pixel-limb-kit`, then run:

```bash
PIXEL_LIMB_LIVE_CONFIRM=disposable-systemd-container \
  bash security-evals/limb-kit-isolation/run-live.sh
```

Never run this harness on a deployment host. Its cleanup intentionally removes the exact
synthetic pack, units, projection state, and gateway test account.
