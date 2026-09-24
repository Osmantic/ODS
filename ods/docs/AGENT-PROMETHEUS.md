# Scraping agent monitoring with Prometheus

The dashboard API exposes its cached agent snapshot at
`GET /api/agents/metrics.prom`. It requires the same Bearer API key as
`/api/agents/metrics` and returns Prometheus text format 0.0.4. Scraping does
not call upstream services or start another collector.

Point a Prometheus scrape job at the dashboard API's reachable address, set
`metrics_path: /api/agents/metrics.prom`, and configure Bearer authorization
with the dashboard API key. Keep that key in your existing secret storage.
The endpoint does not change ODS port bindings or expose a public listener.

| Gauge | Meaning |
| --- | --- |
| `ods_agent_summary_entries` | Number of entries in the last Token Spy summary, not necessarily live sessions |
| `ods_cluster_gpus` | Node count in the last cluster snapshot |
| `ods_cluster_healthy_gpus` | Healthy node count in that snapshot |
| `ods_cluster_failover_ready` | 1 when that snapshot has more than one healthy node, otherwise 0 |
| `ods_agent_output_tokens_per_second_24h` | Latest sampled output-token total divided by the 24-hour summary window |
| `ods_agent_throughput_sample_timestamp_seconds` | Unix timestamp of that observation |

All values are gauges. Do not apply `rate()` to them. The output-token rate
is a **24-hour average**, not instantaneous generation speed. Throughput and
its timestamp are omitted until a retained observation exists; a successful
zero observation is exported as zero. Use
`time() - ods_agent_throughput_sample_timestamp_seconds` to inspect observation
age. An absent timestamp means no retained observation, not confirmed inactivity.

These metrics mirror cached data. A successful scrape means the dashboard API
answered; it does not prove Token Spy or the cluster proxy is reachable.
Cluster values have no observation timestamp in the current collector.
Queue depth and error rate are omitted because the collector has no source
for those placeholder fields. No session identifiers or node labels are emitted.

Wire format: [Prometheus exposition formats](https://prometheus.io/docs/instrumenting/exposition_formats/).
Remove the scrape job to stop collecting; this endpoint writes no persistent state.
