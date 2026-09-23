# Frontier live qualification

Pixel's live qualification is a deployment-owned proof that the installed Frontier
boundary can complete one real, structured provider turn. It is not part of the normal
offline release gate and it is never run automatically. A passing receipt requires the
deployment owner to provide an already configured Frontier credential or saved ChatGPT
Codex login and to explicitly authorize provider usage.

The workflow cannot accept a prompt, file, URL, secret, account identifier, or arbitrary
payload. It generates one byte-stable `public` / `structural` capsule containing only
fixed synthetic text; random qualification identifiers remain local metadata and never
enter provider-facing content. The broker forces that request to `propose`, so preparation
cannot transmit it.

## ChatGPT plan or credits

Use this mode only when the installed broker is configured with `authMode: "chatgpt"`
and a private saved Codex ChatGPT login. The consent file contains no login data.

```bash
./pixel frontier-live-qualify authorization \
  --auth-mode chatgpt \
  --output /secure/client-config/frontier-live-authorization.json \
  --authorize-one-synthetic-provider-call \
  --authorize-chatgpt-plan-or-credits

./pixel frontier-live-qualify prepare \
  --authorization /secure/client-config/frontier-live-authorization.json
```

`prepare` checks the saved login locally, publishes only the fixed synthetic request,
and returns a job ID, plan hash, confirmation hash, inspection command, and exact confirm
command. It does not call the provider. Inspect the capsule with the displayed
`./pixel frontier-show JOB_ID` command.

Run the displayed confirm command only after that inspection. Its final `--transmit`
flag is mandatory. Confirmation may use an eligible ChatGPT plan allowance or credits;
Pixel does not describe it as API usage and does not claim a dollar price for it.

## Separately billed API Platform mode

API-key qualification is a distinct billing boundary. It requires a custom private
Frontier policy with a metered USD cost model whose evidence is no more than 31 days old,
plus an explicit micro-dollar ceiling. One US dollar is 1,000,000 micros; this workflow's
hard ceiling is one dollar.

```bash
./pixel frontier-live-qualify authorization \
  --auth-mode api-key \
  --output /secure/client-config/frontier-live-api-authorization.json \
  --max-estimated-cost-micros 100000 \
  --authorize-one-synthetic-provider-call \
  --authorize-api-billing

./pixel frontier-live-qualify prepare \
  --authorization /secure/client-config/frontier-live-api-authorization.json
```

The configured API key remains inside the isolated broker. It is never copied into the
authorization file, command line, browser, plan, or receipt. Preparation refuses the
request unless the authorization ceiling covers the policy's worst-case estimate for
12,000 input tokens and 256 output tokens, not merely the smaller capsule estimate.

## Fixed safety bounds

- one provider call maximum;
- one fixed synthetic public/structural request, with no caller-supplied content;
- 12,000 input-token accounting ceiling and 256 output-token ceiling;
- one-dollar hard API estimate ceiling, with a lower deployment-owned authorization;
- short-lived, mode-0600, single-link authorization file;
- exact plan, capsule, policy, authorization, expiry, and confirmation-hash binding;
- unique authorization consumption and immutable approval;
- tool-disabled, ephemeral, read-only Codex execution through the existing broker;
- content-free `pass`, `fail`, or `inconclusive` receipt with call, token, billing, and
  estimated-cost evidence but no prompt, response, credential, account, job, provider,
  or model identifier.

The provider's returned advice remains untrusted even when qualification passes.

## Recovery

If confirmation times out or the operator process is interrupted, do not create another
authorization or prepare another job. Re-run the same exact confirm command. The broker's
private immutable authorization claim either returns the already stored receipt, safely
finalizes terminal evidence, or emits an `inconclusive` interrupted-after-approval receipt.
It never starts a second provider execution for that claim.

Use `./pixel frontier-live-qualify show QUALIFICATION_ID` to read the local content-free
state. A `fail` or `inconclusive` result is not release evidence. The network-disabled
isolation rehearsal intentionally produces a failure receipt and proves only the boundary;
it cannot replace a deployment-owned live `pass` receipt.
