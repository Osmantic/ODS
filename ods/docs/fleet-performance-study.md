# ODS Fleet Performance Study (Design)

Status: study protocol plus an implemented engine/gateway pilot; a read-only resource sampler and descriptive pilot comparator are implemented; full agent/UI and capacity adapters remain to be implemented. The small laptop instrumentation pilot does not qualify an optimization. Baseline commit: `0cb226fdb78cda56397b46211e6f6f93ebbe6983`.

This study measures serving performance of the ODS fleet without weakening model quality, verification, or safety. It is not an MLPerf submission and makes no MLPerf compliance claim.

## 1. Scope and non-goals

In scope: validated task completion latency, useful goodput, sustainable capacity, and quality-preserving serving changes on the active product test targets.

Out of scope: GPU caps/fan/hardware campaigns, Tower1 workloads, silent model/quantization/context-loss changes, bypassing Pixel terminal verification, and automatic setting application or promotion. The owner retirement override excludes Tower3 from new benchmarks and engineering workloads. Preserve its earlier installation/regression evidence separately; no new performance workload is assigned to it. Tower2 supplies engineering inference and is also the existing remote-serving route to measure when selected by ODS; quiesce engineering requests during those measured windows.

## 2. Fleet targets and current facts

| Target | Hardware | OS | Role |
|---|---|---|---|
| Tower3 | RTX 5090 | Ubuntu | Retired from new performance workloads; historical installation evidence only |
| MacMini | Apple Silicon 16GB | macOS | Product study |
| Strixy | AMD 8060S | Windows/WSL | Product study |
| Laptop | RTX 5070 Laptop 8GB | Windows/WSL | Product study; optional remote Tower2 |
| Tower2 | Dedicated remote inference service | Verify privately | Engineering inference; measure the existing ODS remote route in isolated windows |

Inventory snapshot, 2026-11-15 17:20 UTC: Laptop image `llama.cpp server-cuda-b11429`, 8151 MiB VRAM, 5886 MiB used idle; container LiteLLM 1.90.7; Dashboard reports `configuredMode=effectiveMode=containerMode=cloud`, ready/healthy/matching. The configured remote provider is Tower2; runtime provider identity must still be checked for every measured request. Mac and Tower3 inventories were refreshed at 17:27 UTC. These observations seed the study; fresh identity checks are required for each experiment. Mac now reports an Apple M4/16 GB host running native llama.cpp, Qwen3.5-9B-Q4_K_M, 65,536 context, one slot, f16 K/V caches. Tower3 now reports RTX 5090/32 GB, llama.cpp CUDA b11429, Qwen3.5-27B-Q4_K_M, 65,536 context, batch 2,048 and one slot. Binary/model hashes and full effective settings are still needed before causal comparisons. Strixy labels remain historical until its inventory succeeds.

Fresh inventory is mandatory. Do not infer engine, backend, flags, or model from hardware labels.

## 3. Measurement principles

- Fixed model, revision, tokenizer, quantization, sampling, prompt/tool contracts when comparing serving changes. Model/quant/context-loss changes are a separate frontier requiring quality requalification.
- Pixel terminal-only SSE is verified in current source: answer bytes are withheld until host verification completes. Optimization cannot bypass verification to fake first-token improvement.
- Separate milestones: first visible progress, first visible answer, first engine content, first reasoning, final validated completion.
- SSE chunk intervals are not token ITL.
- Authoritative per-request token counts; monotonic clock in a single process; never global server counters or word counts.
- Wall-normalized correct-task throughput; output tokens descriptive only; all failures retained.
- Native tools for kernel/prefill/decode; HTTP engine and gateway for route; real Pixel for tools/multiturn; browser Portal for render/interaction.
- Paired trace IDs and sanitized artifacts. No API keys or session-content dumps.
- Unsupported metrics are `null` with a reason.

## 4. Workload manifest

Concrete families, each with independent held-out prompts/repositories:

1. Structured JSON/extraction.
2. Read-only tools multi-hop.
3. Code fix/test sandbox.
4. Replay search, then live web as a separate family.
5. Long-context position/distractors and multiturn history.
6. Plugin install/use/revoke with disposable fixture.
7. Permission/cancellation/retry/runaway safeguards.

Browser extension/Portal UI: repeated cold/warm connect, tool activity, render first answer, cancel, reload, history, composer responsiveness.

Cloud integration: read-only/synthetic account only with existing auth. External sends not approved.

Fixture tasks are not repeated same-prompt claims of broad coverage.

## 5. Phases

1. Inventory: hashes of actual model, backend, flags, driver, build, power profile, cache state, config provenance.
2. Low-load pilot: ~12Ã¢â‚¬â€œ24 requests/cell, max concurrency 1 first.
3. Paired serving A/B over randomized balanced blocks/day-cycles.
4. Concurrency staircase 1, 2, 4, 8 bounded, and open-loop arrivals separately (fixed offered rate, queue/admission timeouts, dropped jobs in denominator; avoid coordinated omission).

Contexts 2K/16K/48K within actual supported limit plus headroom; actual tokenizer counts required, not char buckets. Outputs 64/256/1024 controlled and comparable. Cold engine separate from warm engine prefix miss/hit; OS file cache actual, not assumed; do not label first request cold.

Report p50/p95/p99 with N, right-censoring, CI. p95 N>=100 and p99 N>=1000 are descriptive policy, not proof of precision. Sustainable capacity = highest offered load meeting predeclared quality+latency+stability over duration with headroom and held-out confirmation; not max simultaneous connections. Adaptive knee follow-ups.

Abort faults/OOM persistent overload; do not clear hardware latch. Shared the shared host lock selected by `ODS_FLEET_HOST_LOCK` (default `/tmp/ods-fleet-heavy.lock`) on Tower2 managed orchestration; all participating workloads take the lock; it does not guarantee external quiet. Stop engineering Tower2 inference during Tower2 serving measurement; record background jobs. Restore prior settings/hashes and confirm health after experiments. Trial timeout/cost budget; no automatic setting apply/promotion.

## 6. Quality no-loss mandate

- All previous golden passes must pass.
- Every baseline-pass/treatment-fail is a regression veto pending blinded review.
- Baseline-failing cases do not automatically make the quality gate pass.
- Paired quality measurement uses same cases and seeds, but sampling is nondeterministic.
- Predeclare categories, human adjudication, rubrics, CIs.
- Do not invent a permitted delta > 0 quality loss. Default margin 0; honest statistical evidence is often inconclusive.
- For all-zero observed regressions, one-sided binomial upper bound `1 - alpha^(1/n)` applies only under independent representative samples; repeated same cases invalidate n. This is an uncertainty bound, not allowed loss or proof of zero.
- Can report zero observed loss scoped to qualified suite; full quality equivalence cannot be proved by finite test.
- Speed-better requires paired block CI and meaningful predeclared threshold, multiple-comparison holdout; do not gate solely on tokens/sec or model judge.
- Configuration promotable only with scoped review-approved evidence, no regressions, supported quality uncertainty/stability; unknown = no qualified claim.
- Include candidate/baseline single-factor difference, multifactor follow-up, rollback.

## 7. Current implementation vs planned

Implemented (starter): stdlib Python `tests/performance/probe.py` engine/gateway OpenAI stream pilot only; manifest provenance; balanced closed loop; strict validators; per-request JSONL + sums; bounded connected-transport deadlines; failure counting. DNS resolution may depend on OS resolver timeouts; loopback/IP endpoints avoid this limitation. The client creates a new HTTP connection per request, which is part of its measured transport cost and is not representative of a pooled production client.

Implemented: read-only host resource sampler and a descriptive paired pilot comparator. Not yet implemented: full agent/UI workload adapters, continuous resource admission integration, open-loop scheduler, and qualification-grade statistics. The pilot comparator always leaves production qualification inconclusive.

The checked-in `pilot.example.json` uses loopback port 9 and a placeholder model. Set a verified endpoint/model and provenance before running. The client only records evidence; it does not acquire host locks, collect hardware telemetry, change engine settings, or qualify an optimization.

## 8. Ranked experiments

1. Repeated prefill / prefix reuse. Hypothesis: stable prefix reuse reduces first-visible-answer latency without quality change. Change: prefix cache on/off, same prompts. Controls: model, quant, sampling, context, output length. Signals: first engine content, first visible answer, final validated completion, prefill ms. Veto: any golden regression. Platforms: all.
2. CPU-GPU offload / context allocation memory on 8GB/UMA. Hypothesis: tuned offload/context fits workload with stable latency. Change: offload layers, context size within supported. Controls: model, quant, prompts. Signals: OOM rate, p95 completion, throughput. Veto: quality regression or instability. Platforms: Laptop, Strixy, MacMini.
3. Batching / KV scheduler context capacity. Hypothesis: batching raises useful goodput at fixed quality. Change: batch/scheduler settings. Controls: offered load, prompts. Signals: goodput, p95, queue time, drops. Veto: quality regression. Platforms: Tower3, Strixy, Laptop.
4. Shared prompt composition caching without history loss. Hypothesis: caching shared composition reduces latency without dropping history. Change: cache on/off. Controls: multiturn history, seeds. Signals: first visible answer, final completion, history fidelity. Veto: any history-loss case. Platforms: all.
5. Terminal verification / tool overhead tracing. Hypothesis: tracing isolates verification cost from engine cost. Change: instrumentation only. Controls: same workload. Signals: stage timings, first visible answer vs final validated completion. Veto: instrumentation changes behavior, leaks sensitive data, or adds material overhead; confirm trace-on/off equivalence. Platforms: all.
6. Route connection pooling and history replay. Hypothesis: pooling reduces route latency without replay errors. Change: pool settings. Controls: same route, prompts. Signals: route latency, replay correctness. Veto: replay/history regression. Platforms: all.
7. Platform backend matchup with exact artifact where feasible, else separate model qualification. Hypothesis: matched backend improves latency at equal quality. Change: backend/artifact. Controls: model revision, quant. Signals: paired latency CI. Veto: quality regression. Platforms: per-target.
8. Speculative decode, last only if acceptance verified. Hypothesis: verified acceptance yields speedup. Change: spec decode on/off. Controls: model, sampling. Signals: acceptance rate, latency. Veto: quality regression. Platforms: Tower3, Laptop.

Prior Mac observations of 94s for a 17K-token prompt and 6s on a later request were uncontrolled. They motivate a cache/prefill study; neither a cache state nor a speedup is established. Pixel buffering is a code-proven path hypothesis.

Implementation priority: instrument before tune.

9. Remote-mode local-model residency. The current laptop is healthy in cloud mode while its local engine holds 5,886 / 8,151 MiB VRAM at idle. [Issue #7807](https://github.com/Osmantic/ODS/issues/7807) describes deliberately retained local rollback readiness. Hypothesis: an opt-in sleep/unload policy can release memory without impairing remote service or reliable fallback. Measure freed VRAM, idle power where available, remote latency, local wake latency and model-switch recovery. Veto: lost rollback, monitor-induced wake loops, a stuck model transaction, or any local quality regression. Do not unload anything until those recovery cases exist.

Existing [draft #8358](https://github.com/Osmantic/ODS/pull/8358) covers vLLM admission/lifecycle work; it is not evidence that engine transitions are qualified. Reuse reviewed primitives when they land, and keep engine comparisons separate from unresolved activation/recovery acceptance.

## 9. References (limited paraphrase; original design, not MLPerf claim)

- llama.cpp llama-bench README: native microbenchmark separation of prefill/decode from serving. https://github.com/ggml-org/llama.cpp/blob/master/tools/llama-bench/README.md
- vLLM benchmarking CLI: serve/latency/throughput tooling patterns. https://github.com/vllm-project/vllm/blob/main/docs/benchmarking/cli.md
- MLCommons inference policies: load scenario and quality separation concepts. https://github.com/mlcommons/inference_policies/blob/master/inference_rules.adoc

We are not claiming MLPerf compliance.

## 10. Operator runbook

1. Confirm commit and inventory hashes; record provenance.
2. Acquire the shared host lock selected by `ODS_FLEET_HOST_LOCK` (default `/tmp/ods-fleet-heavy.lock`) on Tower2 if participating.
3. Run pilot at concurrency 1; validate per-request token counts.
4. Run paired A/B in randomized balanced blocks.
5. Run concurrency staircase and open-loop separately.
6. Run quality suite; apply veto rules.
7. Restore settings/hashes; confirm health.
8. Publish scoped report; unknown = no qualified claim.

## 11. Artifact layout

```
ods/artifacts/<study-id>/
  manifest.json
  inventory/
  raw/<cell>/requests.jsonl
  sums/<cell>.json
  quality/
  report.md
```

Sanitized only; no keys or session dumps.

## 12. Acceptance matrix

Each gate reports `pass`, `fail`, or `inconclusive`. A configuration cannot be promoted while any required gate is inconclusive. The pilot alone cannot pass the complete matrix.

| Gate | Criterion |
|---|---|
| Functional | Previous functional gate preserved |
| Quality | No golden regression; uncertainty reported |
| Latency | Paired CI meets predeclared threshold |
| Capacity | Sustainable load with headroom confirmed |
| Stability | No persistent overload/OOM |
| Provenance | Verified route, model/tokenizer/artifact identities and effective config; unknown blocks causal claims |
| Rollback | Prior settings restored and healthy |

Performance qualification is separate from install-fleet-green.

## 13. Next phase

Deliver the protocol and engine/gateway pilot first. Next implement the host collector, trace-linked real Pixel/browser workload adapters, open-loop scheduler, and paired comparator. Refresh each remote inventory before its first measurement window. Complete a low-load baseline on each active target before selecting the first one-factor optimization. Proceed through reviewed experiment manifests and recovery plans before broader loads and serving changes; the prototype is only the first stage of the active optimization goal.


## 14. Concrete first campaign and decision contract

The matrix is a staged screen, not a full Cartesian product. Preserve the existing install/functional test gate and add a separate `performance-study` result. A target can be install-green while performance remains unqualified.

| Stage | Initial work per target | Exit evidence |
|---|---|---|
| Inventory | Read-only hardware/runtime/route audit; verify active model and versions | `inventory.json`, config/image/model/tokenizer hashes or explicit unknowns; actual upstream proof |
| Instrumentation pilot | 4 deterministic cases Ãƒâ€” 3 repetitions, concurrency 1, cache uncontrolled, fixed 256-token maximum | 12 durable receipts; all protocol/validator failures visible; no capacity claim |
| Engine/gateway baseline | Same task and effective sampling through both routes; 3 randomized route-order blocks, 20 requests per route/block | Per-pair latency differences and route identity; unmatched runs excluded from attribution, retained as failures |
| Real-agent baseline | At least 20 distinct cases per applicable family; 3 repeats across representative histories and seeds; fresh workspaces | Expected tool receipts, file tests, final answer checks and browser outcomes; repeats estimate variability, not independent task coverage |
| One-factor A/B | Predeclare candidate, settings diff, paired cases, primary outcome, sample plan, rollback, held-out confirmation | Per-target evidence; no averaging away a slower or regressed target |
| Capacity | Closed-loop staircase first; then bounded open-loop rates around the observed knee | Scheduled/completed/rejected/timed-out counts, quality-conditioned goodput, queue growth, resource headroom, recovery |

Begin the long-context and concurrency cells only after the preceding low-load cases are stable. A manifest caps requests, wall time, in-flight work, context, output and memory-pressure stop conditions. Capacity qualification is blocked until the load generator, telemetry collector and comparator are implemented and validated. Start capacity trials with 10-minute measurement windows, then confirm the candidate rate over 30 minutes on a separate run. These are application-serving windows, not a hardware qualification campaign. Extend if variability or confidence warrants it; duration alone is not acceptance.

For the first comparison predeclare a practical improvement threshold (initial proposal: at least 10% lower median validated task latency or 10% higher SLO goodput). Use a paired block bootstrap over independent task/block units and a 95% confidence interval for the improvement; require the lower bound to exceed the chosen threshold. Report tail latency separately with uncertainty and forbid an unreviewed tail regression. Do not select the fastest noisy cell and reuse its tuning cases as confirmation. Correct multiple comparisons or use an untouched confirmation set.

SLOs are workload-specific input to the manifest. The pilot uses a 10-second completion threshold only to exercise the goodput calculation; it is not an agreed product SLA. Freeze real-agent completion, first-visible-progress and cancellation budgets before A/B. Choose these from the intended user journey and baseline evidence, not after seeing the winning configuration. Report both absolute latency and whether it meets the frozen budget. Do not combine 2K chat, 48K retrieval and multi-step coding into one global percentile.

Capacity = maximum independently confirmed offered task rate that meets those frozen SLOs and quality checks, without growing queues, OOMs, unexplained fallback or resource exhaustion. Publish a lower operating recommendation with 20% initial headroom, separately labelled as policy rather than measured capacity. An unavailable metric or incomplete quality family makes the affected claim `inconclusive`, not pass.

### Trace and artifact contract

Every run needs a study ID, exact ODS base/candidate commit, configuration diff, schema version, task-set/validator version and hash, model/tokenizer/artifact hash, effective sampling, actual engine/backend/build and upstream, cache evidence, host power mode/temperature/background-load observations, and clock origin. Record unverified identities as unknown; reject them for causal A/B claims. Treat declared model IDs and server-reported aliases separately from artifact identity.

Per task, link request/turn/tool/span IDs and record scheduled time, client dispatch, server queue/prefill/decode where instrumented, first content/reasoning, each tool start/end, verification start/end, final delivery, browser first progress/answer, final validation and cancellation outcome. Use durations within each process; cross-host timestamps need measured synchronization error and cannot simply be subtracted. Keep host telemetry aligned at a documented interval, initially 1 second, with observer overhead measured. Record GPU allocation/peak, process RSS, system RAM/swap, CPU, model residency, errors and throttling when exposed; unsupported counters stay null.

The public summary contains counts, hashes, aggregate timings and sanitized failure categories. Private raw outputs and tool/file evidence are retained under controlled access for adjudication; do not publish prompts, keys, account data, full environment dumps or session histories. The starter client stores only answer hashes/lengths and cannot replace that later private adjudication store.

### Workload acceptance examples

| Family | Concrete case | Required proof |
|---|---|---|
| Structured/extraction | Sort IDs and return a typed JSON object; extract Unicode fields with distractors | Exact keys, types and values; no prose/fences or truncation |
| Tools | Read a fixture, calculate a result, update only an allowed scratch file | Correct tool/arguments/order, exact final file and no forbidden calls |
| Coding | Repair a seeded bug in a pinned small repository | Hidden unit/property tests, diff scope, clean repeat run; failing test deletion is a veto |
| Search | Answer from pinned replay pages with conflicting dates | Correct supported answer and citations; live network run reported separately |
| Long context/history | Retrieve changing facts at beginning/middle/end; honor a later correction | All relevant facts and history preserved; no silent truncation or dropped permissions |
| Extensions/integrations | Install/use/revoke a disposable plugin; retry a denied operation | Actual tool result, permission enforcement and confirmed revocation; external sends excluded |
| UI/recovery | Send, observe progress, cancel, reload, reopen history and switch an already-qualified model | Browser state/screenshots plus backend receipts; responsive controls, no duplicate work or stale route |

A synthetic tool-shaped JSON response is not an agent success. Real Pixel and browser runs must prove the external state or tool outcome. Quality reporting includes every task family, baseline failure, timeout, wrong answer and invalid result; a faster refusal or shortened answer is not useful throughput.

### How results improve ODS

Publish a per-surface profile: qualified model/engine/configuration, supported context and concurrency envelope, latency distributions by workload, useful goodput, RAM/VRAM headroom, route choice, quality coverage and rollback instructions. Keep separate model-quality frontiers for any changed weights/quantization. Promote only reviewed profiles with their exact evidence; later engine/model/driver changes invalidate the relevant qualification until rerun. Feed measured results into ODS's evidence-first performance oracle using explicit measurement method and hardware/model/configuration signatures, never importing this small pilot as a universal throughput estimate.

## 15. Frozen experiment manifest requirements

Before an A/B starts, commit an experiment manifest fixing: primary workload/outcome; independent sampling unit; task set and held-out set; paired randomization seed; model/tokenizer/artifact identities; effective per-sequence context; transport connection mode; candidate settings diff; SLOs; block count; sample size; stopping rule; memory/load limits; and analysis version. Pilot and screening samples are exploratory and never promoted into confirmation evidence after results are observed.

Use 10 balanced randomized A/B blocks across at least two time windows for the first confirmation, with at least 20 distinct tasks per selected family per block where feasible. Treat task identity as the resampling cluster across repeated seeds/blocks; use a two-level task/block bootstrap to respect shared load effects. Calibrate the necessary sample size from independent baseline variability before freezing the confirmation manifest; those counts are a starting floor, not a power guarantee. Report the effect-size confidence interval and achieved precision; inadequate power remains inconclusive. Freeze the full sample plan and do not stop early on a favorable confidence interval. Abort only for recorded fault/overload conditions; a new plan requires a new run ID and retains the aborted evidence. Confirm one selected candidate on untouched tasks/windows; if confirming several candidates, predeclare a Holm correction for the family of primary comparisons.

Keep performance and quality gates separate. Report paired per-case quality outcomes, including baseline-pass/candidate-fail and baseline-fail/candidate-pass. A deterministic golden failure vetoes promotion. For subjective cases, use a blinded fixed rubric and independent adjudication before unblinding latency; disagreement is unresolved/inconclusive, never automatically a pass. Repeating an identical prompt is not independent quality evidence. The one-sided regression-risk bound is reported alongside latency, not combined into a speed/quality score. No positive quality-loss margin is authorized. Finite evidence can support only a scoped no-observed-regression qualification; it cannot prove universal equivalence. A claim of statistical population noninferiority at zero margin requires a predeclared valid paired test and sufficient evidence; otherwise that stronger claim stays inconclusive and must not be attached to the profile.

For route attribution, hold the client location, connection reuse mode, request payload, effective model and cache preparation constant. The current fresh-connection pilot can compare that transport mode only. Production pooled connections are a separate control to implement and qualify before attributing user-visible savings to the gateway itself. Do not subtract unlike engine/gateway/UI measurements.

For open-loop confirmation, use seeded exponential inter-arrival times at the declared offered rate. Stamp intended arrival before admission; admit immediately up to a fixed in-flight cap and bounded queue, rejecting excess explicitly. Measure user latency from intended arrival, not from eventual dispatch. Record load-generator lag and scheduled arrivals missed by the generator as invalid measurements/failures, never silently reduce the denominator. As initial strict criteria: no OOM or unexplained route fallback, no rejected/timed-out requests at the claimed operating rate, no monotonic queue growth across three successive one-minute windows, and all frozen workload SLO/quality gates met. Confirm on a different window with new arrival/task seeds and no tuning between confirmation samples. Record external load; if unaccounted competing load or telemetry gaps prevent attribution, mark the trial inconclusive rather than merely noting it.

For memory experiments, record total and per-sequence KV allocation. Increasing `--parallel` must not silently divide the original context budget into smaller per-slot windows. Keep the same effective context allowance; if it does not fit, report that capacity boundary. Do not silently change K/V precision, weight quantization, history retention or output caps to win a serving comparison. KV precision changes are explicit numerical-quality experiments with the same full quality veto. Before increasing load, require no existing OOM/critical latch, no active swap thrash, and a target-specific free system-memory reserve (initial floor 2 GiB on 16 GiB hosts, 10% on larger hosts). Stop admission immediately on OOM, critical hardware status, sustained growing swap-in/out or loss of that reserve. Restore the recorded baseline before proceeding. The telemetry/stop controller must enforce this; the small probe alone does not.

Execution authorized by the updated goal proceeds through those manifests, experiments, reviewed ODS improvements and final upgrades. Final acceptance requires a clean install/lifecycle/model-switch/extensions/agent/UI fleet run on Tower3, Mac Mini, Strixy and laptop using the selected configurations, plus regression tests for every shipped change. Stop tuning a surface after the predeclared candidate set and held-out confirmation show no further practically meaningful qualified gain; record untested or infeasible alternatives and do not claim a mathematical global optimum.
