# Legacy OpenClaw extension removed

ODS deprecated its legacy OpenClaw extension on 2026-05-12 and has now removed
it: the `ods-openclaw` container (image `ghcr.io/openclaw/openclaw:2026.3.8`,
port 7860), its configuration templates and its installer options. It still
pinned OpenClaw 2026.3.8, an older release than the 2026.6.33 runtime that
Pixel qualifies.

The supported agents are [Portal (Pixel)](PIXEL.md) on qualified hosts and
[Hermes Agent](HERMES.md). Pixel runs its own OpenClaw runtime as the host
service `openclaw-gateway.service`. That runtime is separate from this
extension and is not affected.

## Before you update a git checkout

This applies only when ODS runs from a git checkout and you update with
`ods-update.sh update` or the Dashboard update action. Installer reruns do not
need it.

If OpenClaw is enabled, disable it first:

```bash
ods disable openclaw
```

The updater of the release you are leaving restarts the stack with its old file
list, so it stops with an error once the pull deletes the OpenClaw files.
Disabling OpenClaw takes it out of that list. After the update,
`extensions/services/openclaw` holds only the `compose.yaml.disabled` file that
`ods disable` left; you can delete that folder.

If `git status` lists `config/openclaw/openclaw.json` as modified, move it out
of the checkout before you update, and keep the copy private. While OpenClaw
was enabled, the installer wrote your model name into that tracked file, and on
Strix Halo tiers your LiteLLM key. `git pull` refuses to delete a changed file,
so the update would stop with "Git pull failed."

```bash
mv config/openclaw/openclaw.json ~/openclaw.json.bak
chmod 600 ~/openclaw.json.bak
```

If an update already stopped after the pull because OpenClaw was still
enabled, run `ods-update.sh update` again. The pull has already installed the
updater of this release, which finishes the update.

## What an upgrade does

- Rerunning the installer on Linux, macOS or Windows deletes
  `extensions/services/openclaw` from the install directory and removes the
  `ods-openclaw` container when it starts the stack. In a git checkout,
  `git pull` deletes the files; the updater from this release on resolves the
  stack again after the pull, so its restart removes the container.
- The installers delete the OpenClaw templates in `config/openclaw` that are
  unchanged from a shipped version, and remove the folder when nothing else is
  left in it. When OpenClaw files remain, the installer names the folders it
  kept.
- On Linux, a rerun leaves the extension files and templates in place while an
  unfinished Pixel source upgrade is pending. Finish or roll back that upgrade
  with the release that started it; the next rerun of this release then cleans
  up.
- The installers no longer turn OpenClaw back on when they find its container
  or data.
- If OpenClaw was the only feature that needed SearXNG or APE, the upgrade
  turns those services off and removes their containers.
- `--openclaw` and `--no-openclaw` (Linux and macOS) and `-OpenClaw` (Windows)
  are still accepted. They print a notice and change nothing.
- Installers no longer write `OPENCLAW_TOKEN`, `OPENCLAW_PORT` or `HOST_LAN_IP`
  to `.env`. Linux and Windows reruns rewrite `.env` without them; macOS keeps
  existing values. Retired keys that remain are ignored, still pass `.env`
  validation, and can be cleared in the Dashboard settings.
- On AMD Linux installs, a rerun stops and deletes the
  `openclaw-session-cleanup` user timer if it still has the definition ODS
  shipped.
- `ods start` warns while an `ods-openclaw` container still exists, for
  example after an installer run that stopped early.

Nothing migrates to Hermes or Portal. OpenClaw sessions, memories and cron jobs
do not transfer. n8n workflows that call port 7860 stop working; the bundled
`config/n8n/hermes-agent-trigger.json` workflow targets Hermes instead.

## What stays on disk

The upgrade deletes no owner data. These remain until you remove them:

- `data/openclaw/`: agent state. On macOS and Windows,
  `data/openclaw/home/openclaw.json` contains the gateway token, and on macOS
  also a provider key.
- `config/openclaw/`: files you changed or added, such as `workspace/` and a
  changed `openclaw.json`. On Strix Halo tiers, `openclaw.json` can contain a
  copy of your LiteLLM key.
- Retired `.env` keys, pre-update backups in `data/backups/` that include a
  `config-openclaw` copy, and the downloaded OpenClaw image.
- On AMD Linux installs, the `memory-shepherd-memory` and
  `memory-shepherd-workspace` user timers, which reset files in
  `config/openclaw/workspace`.

## Removing the leftovers

Remove a remaining `ods-openclaw` container before the folders: it reads
`config/openclaw` when it starts, so it fails on every restart once the folder
is gone.

On Linux or macOS, from the install directory (`~/ods` by default):

```bash
# Only if `docker ps -a` still lists it.
docker rm -f ods-openclaw

# AMD Linux: stop the timers that maintain the old workspace.
# They fail on every run once config/openclaw is gone.
for timer in openclaw-session-cleanup memory-shepherd-memory memory-shepherd-workspace; do
    systemctl --user disable --now "$timer.timer" 2>/dev/null
    rm -f ~/.config/systemd/user/"$timer".timer ~/.config/systemd/user/"$timer".service
done
systemctl --user daemon-reload

# Optional: keep an archive only you can read; it contains the secrets listed
# above. Name only the folders that still exist.
(umask 077; tar czf ~/openclaw-archive.tgz data/openclaw config/openclaw)

rm -rf data/openclaw config/openclaw

# ODS pulled the image by digest, so it may have no tag.
docker image rm ghcr.io/openclaw/openclaw@sha256:7b1294f6aa2eb05b2070cc614743f79212313fc294e5de221ada8a2969ea52f6
# Older releases pulled other versions; list what is left:
docker image ls --digests ghcr.io/openclaw/openclaw
```

On Linux these folders can belong to UID 1000, the container's user; use
`sudo` for `tar` and `rm` if they report permission errors.

On Windows, from `%USERPROFILE%\ods`, naming only the folders that still exist:

```powershell
docker rm -f ods-openclaw
Remove-Item -Recurse -Force data\openclaw, config\openclaw
docker image rm ghcr.io/openclaw/openclaw@sha256:7b1294f6aa2eb05b2070cc614743f79212313fc294e5de221ada8a2969ea52f6
docker image ls --digests ghcr.io/openclaw/openclaw
```

You can also delete the retired `OPENCLAW_*`, `HOST_LAN_IP` and
`BOOTSTRAP_MODEL` lines from `.env`, or clear them in the Dashboard settings;
ODS ignores them either way.
