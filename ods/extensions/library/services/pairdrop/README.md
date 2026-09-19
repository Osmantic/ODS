# PairDrop — local browser file transfer

Send files directly between browsers. The receiving person can decline a request before accepting its contents. This recipe pins PairDrop 1.11.2, runs Node as UID 1000 with a read-only filesystem, and uses the local server only for discovery and signaling. WebSocket file relay is disabled. Its explicit WebRTC configuration has no external STUN or TURN servers.

## Enable

Copy this directory into `extensions/services/pairdrop/`, then run `./ods enable pairdrop` from the ODS installation. Open `http://localhost:8104` in both browsers, select the other device, choose a file and confirm receipt in the destination browser. Set `PAIRDROP_PORT` in `.env` before enabling to choose another published port. No GPU or persistent server data is required.

The default bind is loopback. For two physical devices, arrange trusted access through your existing HTTPS reverse proxy and forward WebSocket upgrades. Configure the proxy's client-IP forwarding according to the [PairDrop hosting guide](https://github.com/schlagmichdoch/PairDrop/blob/v1.11.2/docs/host-your-own.md). There are no user accounts: everyone able to access the same discovery group can offer transfers. Docker/proxy address translation may place visitors in the same group. Do not publish this unauthenticated UI to the Internet.

Direct host candidates must be reachable between the browsers. This configuration does not promise transfer across NAT, isolated guest Wi-Fi or restrictive firewalls; it deliberately does not contact Google's default STUN server or provide a TURN relay. Browser WebRTC support and secure-context rules still apply. The server must remain reachable for signaling; "local" does not mean the page can establish new sessions without that server.

## State and verification

Files travel over the browser's WebRTC data channel and are saved through the destination browser. The server has no mounted file library. Paired-device and UI preferences belong to browser storage; clearing it removes that browser's remembered state. Keep downloaded files in your own backup system. Disable with `./ods disable pairdrop`; removing the service does not delete browser downloads.

`ODS_TEST_PAIRDROP_BROWSER=1 python -m pytest -q tests/test_pairdrop_extension.py` uses the installed Compose layout and real Chromium contexts to discover a peer, decline a transfer, accept a second transfer and verify the downloaded bytes. It observes native WebRTC connections and asserts an empty ICE-server list. Requires Docker Compose, PyYAML, pytest and Playwright 1.62.0 with Chromium. Linux amd64 localhost is the automated target; separate physical devices, ARM hosts, Safari/Firefox and mobile share sheets require additional validation.
