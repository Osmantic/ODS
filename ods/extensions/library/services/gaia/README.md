# AMD GAIA

Experimental ODS extension recipe for the AMD GAIA Agent UI.

GAIA is AMD's local AI agent framework for Ryzen AI systems. It includes an
Agent UI, tool-using agents, document workflows, voice, vision, MCP support,
and Lemonade Server integration.

This ODS entry is intentionally conservative:

- It is optional and disabled by default.
- Its host port binds to loopback unless `BIND_ADDRESS` is changed by ODS.
  Inside Docker it is reachable on port 4200 of `ods-network`, like other ODS
  services (see [Network and security](#network-and-security)).
- It persists GAIA state under `./data/gaia`.
- It skips GAIA's first-run model bootstrap by default so enabling the
  extension does not unexpectedly download models.
- It does not claim Ryzen AI NPU/iGPU acceleration from inside Docker. For
  best native acceleration, use AMD's desktop/native GAIA installer and point
  ODS tools at that endpoint where appropriate.

## Install

Install **AMD GAIA** from the dashboard's Extensions page. ODS copies this
recipe to `data/user-extensions/gaia`, builds the image from that directory,
prepares `data/gaia` for the container user (uid 10001) on Linux and starts
the service. Stop, restart or remove it from the same page.

Do not copy this directory into `extensions/services/`. Built-in extension
Compose files resolve relative build contexts against the install root, so
the recipe's `build.context: .` would point at the wrong directory and the
image build fails.

Open the UI at:

```text
http://localhost:${GAIA_PORT:-7822}
```

## Configuration

Set these in `.env` before starting the extension:

```env
GAIA_PORT=7822
GAIA_AGENT_UI_VERSION=0.19.0
GAIA_SKIP_GAIA_INIT=true
GAIA_DISABLE_UPDATE=1
GAIA_LEMONADE_BASE_URL=
```

Use `GAIA_LEMONADE_BASE_URL` when you already have Lemonade Server or a
compatible endpoint available, for example:

```env
GAIA_LEMONADE_BASE_URL=http://host.docker.internal:8000/api/v1
```

The GAIA CLI also reads `LEMONADE_BASE_URL`, `GAIA_BASE_URL`, and
`GAIA_MODEL_ID`; the compose file passes those through for advanced setups.

## Modes

Default mode starts `gaia-ui` and allows the npm package to install the Python
backend into `./data/gaia/venv` on first start. `GAIA_SKIP_GAIA_INIT=true`
prevents the additional Lemonade/model initialization step.

AMD's backend listens on `127.0.0.1` only. The entrypoint therefore runs it on
the container-internal port 4201 and starts `ods-gaia-forward`, a small TCP
forwarder that listens on `0.0.0.0:4200` and relays connections unchanged. The
entrypoint supervises both: if `gaia-ui`, its backend or the forwarder stops,
the container exits non-zero and Docker restarts it. `gaia-ui` logs mention port
4201; open GAIA at the host URL above.

For a lightweight container/UI smoke test only:

```env
GAIA_UI_SERVE_ONLY=true
```

Serve-only mode is useful for validating the extension container and dashboard
link, but it does not start the GAIA Python backend. It serves on port 4200
directly, without the forwarder.

## Network and security

GAIA's API has no authentication. AMD treats loopback as the trust boundary.
In ODS:

- The host port is published on `BIND_ADDRESS`, `127.0.0.1` by default, so
  only the ODS machine reaches `http://localhost:7822`. In LAN mode
  (`BIND_ADDRESS=0.0.0.0`) anyone on that network can use GAIA and its agents
  without signing in, as with other ODS services that have no login of their
  own. Do not enable GAIA on a LAN-mode install whose network you do not trust.
- Any container on `ods-network` can reach `http://gaia:4200`, as with other
  ODS services. The dashboard's health check uses this path.
- Every connection reaches GAIA through the forwarder, so GAIA sees every
  client as `127.0.0.1`. GAIA's ngrok "mobile access" tunnel is the only mode
  with a login, and that login trusts loopback clients. The image contains no
  ngrok, ODS passes no ngrok token, and the entrypoint refuses to start when an
  `ngrok` binary is on the backend's `PATH` (including `data/gaia/bin`). Do not
  add ngrok to this container.

## Known Limitations

- First backend start installs Python 3.12, `amd-gaia[ui]` and CPU PyTorch
  (about 400 MB to download, 1.4 GB in `data/gaia/venv`). On a fast link this
  takes seconds; on a slow link it can take several minutes. The dashboard
  shows the extension as installing for up to 10 minutes (`startup_timeout`).
- Full GAIA behavior is best with Lemonade Server. Generic OpenAI-compatible
  endpoints may support only part of the GAIA workflow surface.
- The container recipe does not install host GPU/NPU drivers or Lemonade Server
  onto the host.
- This entry is experimental and intended to graduate after AMD hardware fleet
  validation.
