# Incident response

Use this runbook for suspected prompt-driven side effects, credential disclosure,
unexpected Operations activity, cross-session/client exposure, compromised dependencies,
or loss of a security boundary. Preserve timestamps and secret-free evidence as you go.

## 1. Contain

1. Stop the gateway and Web Courier if model or browser compromise is suspected.
2. Run `./pixel ops-pause "incident containment" --confirm` when Operations is enabled.
   Revoke every active temporary lease shown by `./pixel ops-authority show`; do not resume
   merely because queued work appears quiet.
   Run `./pixel frontier-pause "incident containment" --confirm` when Frontier is
   enabled, then stop `pixel-frontier-broker.service` if disclosure may be in progress.
3. Disable the affected limb at its service boundary. For Google sources, stop the Source
   Broker timer and action unit before changing files. For a runner, remove its forced-key
   line through the already trusted operator channel or isolate the host from the network.
4. Preserve the exact release/source provenance, relevant journal window, bounded broker
   events/results, host identity, and file hashes. Never copy tokens, private email bodies,
   session text, or private keys into an incident ticket.
5. Use the local Diagnostics panel only for content-free orientation. An `attention` or
   `containment` row identifies a fixed local action category and safe next step; inspect
   the referenced private evidence from a trusted terminal. `unavailable` means the
   retained evidence is incomplete or failed integrity checks. A `clear` panel means only
   that no retained local-control incident is open; it is not proof that the host, brokers,
   credentials, or deployment are safe, and it never authorizes resume.

## 2. Classify and rotate

- Gateway credential: run `./pixel rotate gateway --confirm`; verify the listener remains
  loopback-only and both an old-token request and an unauthenticated request fail.
- Google OAuth: revoke the grant in the client-owned Google Workspace account, remove the
  broker-owned token, rotate an exposed OAuth client secret, then repeat `./pixel authorize`
  and `./pixel source-broker --confirm`. Do not copy the replacement token into the gateway
  account.
- Operations SSH identity: keep Operations paused. Through every independently trusted
  operator alias, remove the old Pixel forced key, generate a replacement isolated broker
  key, re-enroll each target, verify its expected hostname and pinned host key, and prove
  both transport/workload separation paths with `security-evals/runner-boundary/run-live.sh`.
  Resume only after all targets reject the old key.
- Backup signing key: distrust the old signer, generate a new deployment signing key and
  separately export its allowed-signers trust anchor, then create and rehearse a new full
  encrypted backup. Existing backups from the retired signer remain evidence, not trusted
  restore inputs.
- Provider credential: rotate, revoke, or expire it at the issuer and use a safe identity
  or authentication probe to prove the old value is rejected. Removing a bot, account, or
  key from every workspace is immediate containment, but it is not issuer-side revocation.
- Frontier provider key: keep Frontier paused, revoke the API key at its issuer, replace
  the separate mode-`0600` credential source, reinstall the broker, and use only a
  synthetic low-sensitivity probe to prove the old key fails and the new key works.
  Review provider-side usage/account logs and the local content-free usage ledger without
  copying private capsules into the incident record.
- Frontier cache, routing, or quality anomaly: pause Frontier, preserve only bounded
  hashes and content-free ledgers, compare provider-side calls with local call/cache
  counts, and inspect private plans/results under the broker identity. Do not resume
  bounded automation after a quality-circuit event until the cause is understood; remove
  suspect cache entries through the trusted operator path and re-run dedup/tamper gates.
- Knowledge-vault key exposure: stop every affected `pixel-work-*` event/watchdog/service
  unit, preserve content-free lifecycle heads, create a new external 32-byte key under the
  Deep Work service identity, and run the offline exact rotation flow. Do not delete the old
  key until every retained encrypted backup using it has either expired or been restored into
  isolated staging and rotated. Treat loss of the only applicable key as unrecoverable data,
  not as an invitation to weaken authenticated decryption or substitute an empty vault.
- Suspected knowledge resurrection or deletion-ledger damage: keep Deep Work offline and
  preserve the active vault plus backup signature/checksum as private evidence. Validate and
  rehearse the archive first. Use only `./pixel restore` with the historical and current vault
  keys; never copy the historical directory into place. A tombstone conflict, source identity
  mismatch, wrong key, residual transaction, or failed deep audit is a stop condition.
- Model, plugin, container, or package provenance: stop the affected runtime, replace the
  pin/checksum in a reviewed release, rebuild from a clean source tree, and do not reactivate
  the old artifact from cache.

## 3. Eradicate and recover

Use a clean supported host when host integrity is uncertain. Verify the source manifest and
pinned artifacts, configure from the client-private onboarding record, and validate a signed
backup before an isolated rehearsal. An actual restore requires `--replace --confirm` and
must pass post-restore verification; forced failure must demonstrate automatic rollback.
For a knowledge-bearing backup, confirm that the final receipt reports both deletion
reconciliation and removal of historical key wrapping before restarting Deep Work.
If a credential entered Git history, rewrite all writable refs, delete obsolete refs, expire
reflogs, resynchronize every clone and stash, and run both the local history scanner and
`security-evals/assurance/audit_remote_refs.py`. Ask the hosting provider to remove cached
views, hidden pull-request refs, and unreachable objects when the remote audit still finds
them; record the provider confirmation without copying the credential into the ticket.
Re-run the source-injection, control, session, sandbox, runner, Operations, Frontier pressure, and modular
end-to-end gates appropriate to the enabled limbs.

## 4. Return to service

Return only when there is no unresolved P0/P1 finding, affected credentials are rotated,
old credentials are proven rejected, the release has two consecutive clean full passes,
and the owner accepts the remaining risk. Resume Operations and Frontier separately with reasons, start limbs one
at a time, and watch bounded events and service health through the agreed observation window.
Record a secret-free timeline, root cause, affected scope, fixes, regression IDs, credential
owners, and follow-up deadlines.
