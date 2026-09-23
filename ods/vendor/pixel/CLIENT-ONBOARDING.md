# Client onboarding

This page is safe to hand to a client. The operator must never reuse another client's
OAuth project, token, memory, session, model key, or workspace.

## Decisions to collect

- owner and organization names, deployment name, and IANA timezone;
- prepared versus reference profile;
- capability profile and the explicit on/off decision for every limb;
- private model and SearXNG endpoints (or reference model file);
- Google Workspace account and Calendar ID;
- who owns backups, incident response, upgrades, and attendee-notification decisions.
- when Operations is enabled: approved machines, target roots, named jobs, automatic
  tiers, break-glass owner, allowed download domains, and SSH identity custodian.

## Google Cloud setup

In a new client-owned Google Cloud project:

1. Enable Gmail API and Google Calendar API.
2. Configure an Internal OAuth audience when the Workspace policy permits it.
3. Add only `https://www.googleapis.com/auth/gmail.readonly` and
   `https://www.googleapis.com/auth/calendar.events`.
4. Create a Desktop app OAuth client and place its downloaded JSON at the configured
   `PIXEL_GOOGLE_CLIENT_FILE` on the host with mode `0600`.

For a remote host, forward the loopback callback from the operator computer:

```bash
ssh -N -L 8765:127.0.0.1:8765 pixel-host
```

On the host run `./pixel authorize`, open the printed URL in the owner's signed-in
browser, inspect the account and two scopes, then approve. Authorization creates a
short-lived staging file. Immediately run `./pixel source-broker --confirm`; it verifies
the live projections, moves the credential under the dedicated broker system identity,
and removes the gateway owner's staging copy. The refresh token is never printed.

## Owner acceptance exercise

Ask Pixel to list recent projected inbox mail, search for a known harmless message, and
list the next seven days of projected Calendar events. Confirm that the projection
contains summaries and risk flags but no raw body or event description. Ask Pixel to
propose a clearly named temporary event; verify it remains pending until an operator
runs `./pixel source-show ...`, reviews the protected snapshot, and supplies its exact
SHA-256 to `./pixel source-approve ... SHA256 --confirm`. Confirm the result records the
same hash and the event appears after the next refresh, then repeat with a deletion
proposal. Actuators disable attendee notifications in the base deployment. Never use a
real meeting for this test.

Run the controlled email prompt-injection harness before handoff. A passing deployment
must show only projection tools in the Pixel trace and no `exec`, file, memory, network,
or actuator tool caused by the hostile message.

When Operations is enabled, use disposable targets/jobs to run identity, health, I/O,
parallel workflow, cancellation, hostile-output, public-download, and verified-transfer
checks. Confirm a changed target identity is rejected, raw shell waits for external
approval, a modified plan invalidates approval, machine output cannot trigger another
job, and each disabled limb is absent. Do not use production state for acceptance.

## Handoff package

The client receives the version/checksum, customized deployment and limb record, Google
Cloud project ownership, private Operations policy/target inventory where applicable,
backup/recovery instructions, escalation contact, and upgrade window. They do not
receive source-machine sessions, broker credentials, or another owner's data.
