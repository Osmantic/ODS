# AMD GAIA

Experimental ODS extension recipe for the AMD GAIA Agent UI.

GAIA is AMD's local AI agent framework for Ryzen AI systems. It includes an
Agent UI, tool-using agents, document workflows, voice, vision, MCP support,
and Lemonade Server integration.

This ODS entry is intentionally conservative:

- It is optional and disabled by default.
- It binds to loopback unless `BIND_ADDRESS` is changed by ODS.
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

For a lightweight container/UI smoke test only:

```env
GAIA_UI_SERVE_ONLY=true
```

Serve-only mode is useful for validating the extension container and dashboard
link, but it does not start the GAIA Python backend.

## Known Limitations

- First backend start can take several minutes while Python dependencies are
  installed.
- Full GAIA behavior is best with Lemonade Server. Generic OpenAI-compatible
  endpoints may support only part of the GAIA workflow surface.
- The container recipe does not install host GPU/NPU drivers or Lemonade Server
  onto the host.
- This entry is experimental and intended to graduate after AMD hardware fleet
  validation.
