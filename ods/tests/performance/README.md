# Fleet performance pilot

This is the measurement foundation for the [fleet performance study](../../docs/fleet-performance-study.md). It probes one already-running OpenAI-compatible engine or gateway. It does not install or tune engines, execute tools, certify Pixel/UI behavior, or establish sustainable capacity.

Python 3.10+ and the standard library are sufficient. Copy `pilot.example.json` outside the repository, set the verified endpoint/model and provenance, then run from `ods/`:

```sh
python3 tests/performance/probe.py --manifest /path/to/pilot.json --out /path/to/new-run
python3 -m unittest discover -s tests/performance -p 'test_probe.py' -v
```

For authenticated endpoints, add `"api_key_env": "ODS_PERF_API_KEY"` and supply the key in that environment variable using your private credential mechanism. Do not put the value in the manifest or command line. Endpoint redirects, proxies discovered from the environment, and non-loopback plaintext HTTP are not supported. The example port 9 and model ID are deliberately placeholders.

The run writes the normalized manifest and its hash, one JSONL receipt per dispatched request, and a summary. An existing output directory is refused. A nonzero CLI exit means at least one request failed or the run could not complete. Outputs contain the synthetic prompts and expected values from your manifest; use public fixtures only. Model response text is hashed, with a character count, rather than saved. Error bodies and credential values are not recorded.

Requests use balanced, shuffled closed-loop scheduling at the configured concurrency. All model calls request streaming with usage. An endpoint that does not implement that protocol fails visibly; no retries, non-stream fallback, model switching, or automatic authentication fallback occur. Missing token usage remains unknown. First content/reasoning and gaps measure received SSE chunks, not individual tokens. Each request opens a fresh connection; connection setup is included in latency. A watchdog bounds connected transport reads, including trickle streams; DNS resolution depends on the OS resolver and is not covered by that watchdog. Prefer verified loopback/IP routes for this pilot.

Timeouts, protocol errors, identity mismatches and wrong answers remain in the planned-request denominator. Summary latency percentiles cover protocol-complete responses, with successful-task median reported separately. Neither is a claim about the censored/failed requests. p95 is suppressed below 100 completed samples and p99 below 1,000; those thresholds do not establish statistical precision. The 10-second example SLO is a diagnostic threshold, not a product SLA. Repeated four-case fixtures validate instrumentation, not broad model quality.

Run only against an explicit idle target after coordinating its workload window. The prototype does not acquire the fleet lock or sample resources. Keep cache state `uncontrolled`: this adapter cannot prove a cache flush, prefix hit, or cold model. Unknown provenance is permitted for exploration and blocks a qualified causal comparison. See the study for inventory, agent/UI adapters, quality vetoes, paired experiments, and later open-loop capacity work.
