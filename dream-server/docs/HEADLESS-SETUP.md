# Headless Setup and QR Onboarding

Dream Server can be prepared as a local AI appliance: install it on hardware,
ship or hand over the machine without a monitor, and let the recipient finish
setup from a phone, laptop, tablet, or TV browser.

This page is the hardware-neutral map of the code that makes that possible.
Strix Halo, DGX Spark, NUC-style mini PCs, repurposed desktops, and home lab
servers should all follow the same high-level flow once their platform-specific
installer path is working.

## Current Status

The headless setup stack is implemented on `main` across the setup-card script,
dashboard, dashboard API, host agent, mDNS announcer, AP-mode script, and Caddy
proxy extensions.

Status is **deployed in code and partially validated on the real hardware
fleet** for the LAN, mDNS, dashboard, magic-link, and Hermes access paths. The
packaged appliance handoff still needs per-image validation because routers,
Wi-Fi chipsets, AP-mode behavior, and client devices vary. In particular:

- Magic-link generation, QR rendering, first-run state, and dashboard flows have
  unit/integration coverage.
- Wi-Fi management is implemented for Linux hosts with NetworkManager / `nmcli`.
- AP mode is available but intentionally disabled by default because it takes
  over a wireless interface.
- mDNS plus `dream-proxy` provide the friendly LAN URLs. Fleet validation
  covers representative LAN behavior, but each packaged hardware image should
  still be validated on its target router/client environment.

## User Journey

1. The device is pre-installed with Dream Server and given a friendly
   `DREAM_DEVICE_NAME` such as `dream`, `studio`, or `kitchen-ai`.
2. The operator prints or ships a setup card with:
   - QR #1: a Wi-Fi QR code for the device setup AP, if AP mode is used;
   - QR #2: either the setup URL for first-run provisioning, or a permanent
     owner-card magic link that lands the new owner in Hermes.
3. The recipient scans the setup QR, opens the first-boot wizard, and joins the
   device to their home network when needed.
4. Dream Server generates an owner-card magic link and QR code for the first user.
5. After redemption, the user lands in Hermes. Power users can still open the
   dashboard for model, service, and diagnostics controls.

## Main Components

| Component | Code | Tests / Docs | Purpose |
|---|---|---|---|
| Setup card generator | [`scripts/generate-setup-card.py`](../scripts/generate-setup-card.py) | [`tests/test_setup_card.py`](../tests/test_setup_card.py), [`SETUP-CARD.md`](SETUP-CARD.md) | Produces printable cards with Wi-Fi + setup QR codes or Wi-Fi + owner Hermes QR codes. |
| First-run state | [`extensions/services/dashboard-api/routers/setup.py`](../extensions/services/dashboard-api/routers/setup.py), [`extensions/services/dashboard/src/hooks/useFirstRun.js`](../extensions/services/dashboard/src/hooks/useFirstRun.js) | [`tests/test_setup.py`](../extensions/services/dashboard-api/tests/test_setup.py), [`App.test.jsx`](../extensions/services/dashboard/src/App.test.jsx) | Server-side setup sentinel that decides whether the wizard should appear. |
| Phone-first wizard | [`extensions/services/dashboard/src/pages/FirstBoot.jsx`](../extensions/services/dashboard/src/pages/FirstBoot.jsx) | [`FirstBoot.test.jsx`](../extensions/services/dashboard/src/pages/FirstBoot.test.jsx) | Guides first setup and shows the owner-card QR. |
| Magic-link auth and QR | [`extensions/services/dashboard-api/routers/magic_link.py`](../extensions/services/dashboard-api/routers/magic_link.py), [`extensions/services/dashboard/src/pages/Invites.jsx`](../extensions/services/dashboard/src/pages/Invites.jsx) | [`test_magic_link.py`](../extensions/services/dashboard-api/tests/test_magic_link.py), [`Invites.test.jsx`](../extensions/services/dashboard/src/pages/Invites.test.jsx), [`HERMES-SSO.md`](HERMES-SSO.md) | Creates revoke-only owner cards and temporary guest links, renders QR codes, redeems tokens, and issues signed session cookies. |
| Wi-Fi setup API | [`extensions/services/dashboard-api/routers/setup.py`](../extensions/services/dashboard-api/routers/setup.py), [`bin/dream-host-agent.py`](../bin/dream-host-agent.py) | [`test_network_config.py`](../extensions/services/dashboard-api/tests/test_network_config.py), [`test_host_agent.py`](../extensions/services/dashboard-api/tests/test_host_agent.py) | Lets the dashboard ask the host agent to scan, connect, check status, and forget Wi-Fi profiles. |
| First-boot AP mode | [`scripts/ap-mode.sh`](../scripts/ap-mode.sh), [`scripts/systemd/dream-ap-mode.service`](../scripts/systemd/dream-ap-mode.service), [`scripts/ap-mode.conf.example`](../scripts/ap-mode.conf.example) | [`test-ap-mode.sh`](../tests/test-ap-mode.sh), [`AP-MODE.md`](AP-MODE.md) | Optional setup access point for devices that are not yet on the user's network. |
| LAN discovery | [`bin/dream-mdns.py`](../bin/dream-mdns.py), [`scripts/systemd/dream-mdns.service`](../scripts/systemd/dream-mdns.service) | [`MDNS.md`](MDNS.md) | Publishes `<device>.local` and service subdomains on the local network. |
| LAN reverse proxy | [`extensions/services/dream-proxy/Caddyfile`](../extensions/services/dream-proxy/Caddyfile), [`extensions/services/dream-proxy/compose.yaml`](../extensions/services/dream-proxy/compose.yaml) | [`DREAM-PROXY.md`](DREAM-PROXY.md) | Routes `chat.<device>.local`, `dashboard.<device>.local`, `auth.<device>.local`, `api.<device>.local`, and `hermes.<device>.local`. |
| Agent surface | [`extensions/services/hermes-proxy/Caddyfile`](../extensions/services/hermes-proxy/Caddyfile) | [`HERMES.md`](HERMES.md), [`HERMES-SSO.md`](HERMES-SSO.md) | Gates Hermes behind Dream Server magic-link session auth. |

## Operator Prep Checklist

For a hardware image or pre-installed unit:

1. Install Dream Server normally for the target platform.
2. Set a unique `DREAM_DEVICE_NAME` in `.env`.
3. Set `DREAM_SESSION_SECRET` so magic-link redemption can issue signed
   `dream-session` cookies.
4. Enable the LAN entry path:
   - `dream-proxy` for friendly HTTP routing;
   - `dream-mdns` for local `.local` discovery where supported;
   - `hermes-proxy` if the Hermes Agent should be LAN-reachable behind auth.
5. For AP-based out-of-box setup, write `/etc/dream/ap-mode.conf`, install the
   `dream-ap-mode.service` unit, and generate a setup card or factory owner card.
6. Validate the exact QR flow on the target image before shipping.

## Validation Checklist

Use this checklist for each target hardware profile:

- Fresh install completes without a monitor attached.
- Setup card Wi-Fi QR joins the setup AP.
- Setup URL QR opens the first-boot wizard.
- Wi-Fi scan returns nearby networks on Linux NetworkManager hosts.
- Wi-Fi connect succeeds and the device remains reachable after network handoff.
- `dream-proxy` answers on port 80 from another device on the LAN.
- `<device>.local`, `chat.<device>.local`, `dashboard.<device>.local`, and
  `auth.<device>.local` resolve on at least one phone and one laptop.
- Owner-card magic link redeems repeatedly, sets the signed cookie, and redirects
  to Hermes until revoked.
- Temporary guest invite links still redeem, set the signed cookie, and redirect
  to their selected target.
- Hermes is reachable through `hermes.<device>.local` when enabled.
- Mobile voice is validated from HTTPS/Tailscale HTTPS when the packaged Hermes
  surface exposes microphone controls; plain HTTP is expected to show fallback
  readiness rather than promise mic access.
- Dashboard remains available for power users after first-run completion.

## Known Limits

- AP mode is Linux-only and assumes NetworkManager, `hostapd`, `dnsmasq`,
  `iptables`, and a Wi-Fi interface capable of AP mode.
- AP mode is disabled by default because it is intentionally disruptive to a
  wireless interface.
- mDNS behavior varies by client OS, router, VPN, and enterprise network policy.
- The complete AP-mode appliance handoff still needs repeated end-to-end
  testing on each target packaged hardware image.
- Owner cards are physical credentials. They are not device-bound in v1 and
  should be revoked if a card is lost, photographed, or replaced.
