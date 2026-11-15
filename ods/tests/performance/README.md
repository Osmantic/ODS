# Fleet performance pilot

Saved evidence redacts the authentication environment-variable selector as well as its value. Keep your private execution manifest to repeat authenticated requests; the public evidence manifest alone does not reconstruct authentication.

This is the measurement foundation for the [fleet performance study](../../docs/fleet-performance-study.md). It probes one already-running OpenAI-compatible engine or gateway. It does not install or tune engines, execute tools, certify Pixel/UI behavior, or establish sustainable capacity.

Python 3.10+ and the standard library are sufficient. Copy `pilot.example.json` outside the repository, set the verified endpoint/model and provenance, then run from `ods/`:

```sh
python3 tests/performance/probe.py --manifest /path/to/pilot.json --out /path/to/new-run
python3 -m unittest discover -s tests/performance -p 'test_probe.py' -v
python3 tests/performance/compare.py --baseline /path/to/baseline --candidate /path/to/candidate --out /path/to/new-comparison.json
python3 -m unittest discover -s tests/performance -p 'test_compare.py' -v
```

For authenticated endpoints, add `"api_key_env": "ODS_PERF_API_KEY"` and supply the key in that environment variable using your private credential mechanism. Do not put the value in the manifest or command line. Endpoint redirects, proxies discovered from the environment, and non-loopback plaintext HTTP are not supported. The example port 9 and model ID are deliberately placeholders.

The run writes the normalized manifest and its hash, one JSONL receipt per dispatched request, and a summary. An existing output directory is refused. A nonzero CLI exit means at least one request failed or the run could not complete. Outputs contain the synthetic prompts and expected values from your manifest; use public fixtures only. Model response text is hashed, with a character count, rather than saved. Error bodies and credential values are not recorded.

Requests use balanced, shuffled closed-loop scheduling at the configured concurrency. All model calls request streaming with usage. An endpoint that does not implement that protocol fails visibly; no retries, non-stream fallback, model switching, or automatic authentication fallback occur. Missing token usage remains unknown. First content/reasoning and gaps measure received SSE chunks, not individual tokens. Each request opens a fresh connection; connection setup is included in latency. A watchdog bounds connected transport reads, including trickle streams; DNS resolution depends on the OS resolver and is not covered by that watchdog. Prefer verified loopback/IP routes for this pilot.

Timeouts, protocol errors, identity mismatches and wrong answers remain in the planned-request denominator. Summary latency percentiles cover protocol-complete responses, with successful-task median reported separately. Neither is a claim about the censored/failed requests. p95 is suppressed below 100 completed samples and p99 below 1,000; those thresholds do not establish statistical precision. The 10-second example SLO is a diagnostic threshold, not a product SLA. Repeated four-case fixtures validate instrumentation, not broad model quality.

Run only against an explicit idle target after coordinating its workload window. The probe does not acquire the fleet lock or automatically sample resources. Keep cache state `uncontrolled`: this adapter cannot prove a cache flush, prefix hit, or cold model. Unknown provenance is permitted for exploration and blocks a qualified causal comparison. See the study for inventory, agent/UI adapters, quality vetoes, paired experiments, and later open-loop capacity work.

## Resource sampling and admission control

Run the read-only sampler separately on the target, with an explicit scope:

```sh
python3 tests/performance/telemetry.py --output /path/to/new-resources.jsonl --duration 60 --interval 2 --scope host
```

Use `--scope wsl` or `--scope container` for those boundaries; never add them to
host memory totals. `--nvidia` enables a fixed, two-second-bounded `nvidia-smi`
query. It performs no GPU control operations. Unsupported metrics remain null.
Linux reports MemAvailable. Windows keeps physical availability and commit
headroom separate. macOS reports page categories, swap counters and a read-only
pressure signal; it does not fabricate an available-memory total or dedicated
GPU memory. Its pressure sysctl is a private OS interface: unknown values or
missing access remain explicit errors. The dispatch mapping is documented in
[Apple's XNU source](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_memorystatus_notify.c).

The sampler records monotonic timestamps, collection elapsed time, its own
process CPU time and scheduling lag. Child-command CPU time is not included.
An overlong sample skips catch-up work, and a terminal marker records normal or
interrupted completion. Collection can extend beyond the nominal duration by
the current bounded sample. It does not stop a model or enforce admission.
Measure collector on/off overhead before using it for an optimization claim.

Python coordinators may call `probe.run(manifest, output, admission_allowed=fn)`.
The callback must return `True` before each new request; any other return stops
new admission and drains already admitted requests within their request
deadlines. The full planned denominator and completed receipts remain in the
summary, with `admission_stopped=true`. Callback exceptions retain partial
evidence and propagate. Freeze and record resource/elapsed-time stop rules in
the external coordinator. This hook alone is not a full admission controller.


## Fixed-arrival load pilot

`open_loop.py` uses the same engine/gateway manifest and validators as `probe.py`,
with a separate load configuration:

```json
{"rate_rps": 1.0, "max_schedule_lag_ms": 100}
```

```sh
python3 tests/performance/open_loop.py --manifest pilot.json --load-config load.json --out new-run
```

Arrivals follow a fixed schedule regardless of request completion. There is no
client waiting queue: at most the manifest concurrency (maximum 8) is in flight.
An arrival when the client is full gets a `client_busy` refusal receipt. Late
arrivals get `scheduler_late` rather than a catch-up burst. Both are client load
generator limits, not evidence of a server rejection. Every planned arrival is
accounted for, including those refused after an admission stop. Admission callback
errors drain current requests, save evidence, then propagate. An interrupt also
drains current requests and records the remaining unattempted arrivals.

The generator permits up to 1,000 arrivals over at most 600 seconds. In-flight
requests can extend the duration by their request deadlines; DNS remains outside
the socket watchdog. A slow callback or busy client can invalidate the intended
offered load; inspect schedule lag and refusal counts before interpreting results.

Goodput is validated tasks divided by the whole observed duration, including the
drain. Success fraction uses all planned arrivals. Arrival-to-completion latency
includes scheduling and worker handoff time; refusal latency is unknown. The
summary separates protocol completion, quality, identity, transport, and internal
failures. p95 is suppressed below 100 successful observations. This is descriptive
engine/gateway evidence, not a production capacity or agent-quality qualification.
Use the same model, workload, host observability, and load settings in comparisons.
The existing paired comparator supports closed-loop probe receipts only.
