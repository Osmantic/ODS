# Fleet performance pilot

Saved evidence redacts the authentication environment-variable selector as well as its value. Keep your private execution manifest to repeat authenticated requests; the public evidence manifest alone does not reconstruct authentication.

This is the measurement foundation for the [fleet performance study](../../docs/fleet-performance-study.md). The engine probe targets one already-running OpenAI-compatible engine or gateway. The separate Portal adapter below submits real Pixel tasks. Neither installs or tunes engines, certifies UI behavior, or establishes sustainable capacity.

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

Optional `sampling.thinking_budget_tokens` passes a thinking budget to backends that support this field, such as llama.cpp. It must be an integer from zero to less than `max_tokens` (which defaults to 128); omit the field to use the backend's default. It does not enable thinking or increase the total output ceiling, and it does not guarantee room for a complete final answer. Verify that the selected backend accepts and honors the budget; this is not a universal OpenAI-compatible parameter.

Optional sampling-profile fields `sampling.top_k` (integer 0..2147483647), `sampling.min_p` (number 0..1), `sampling.presence_penalty` (number -2..2), and one of `sampling.repeat_penalty` or `sampling.repetition_penalty` (number > 0, mutually exclusive) are forwarded verbatim to the backend when present; omit any field to leave the backend default untouched. Native llama.cpp uses `repeat_penalty`; other OpenAI-compatible providers may use `repetition_penalty` instead. The probe does not rename, translate, or inject defaults for these fields, and it does not verify that the selected backend actually accepts or honors them: effective backend acceptance still requires separate live proof against the target engine or gateway. Unknown or reserved sampling keys are rejected at manifest validation. Booleans and non-finite values are rejected. `top_k=0` disables top-k filtering on backends supporting that convention. The requested fields are included in the normalized manifest and its hash.

Timeouts, protocol errors, identity mismatches and wrong answers remain in the planned-request denominator. Summary latency percentiles cover protocol-complete responses, with successful-task median reported separately. Neither is a claim about the censored/failed requests. p95 is suppressed below 100 completed samples and p99 below 1,000; those thresholds do not establish statistical precision. The 10-second example SLO is a diagnostic threshold, not a product SLA. Repeated four-case fixtures validate instrumentation, not broad model quality.

Run only against an explicit idle target after coordinating its workload window. The probe does not acquire the fleet lock or automatically sample resources. Keep cache state `uncontrolled`: this adapter cannot prove a cache flush, prefix hit, or cold model. Unknown provenance is permitted for exploration and blocks a qualified causal comparison. See the study for inventory, agent/UI adapters, quality vetoes, paired experiments, and later open-loop capacity work.

## Single Portal task adapter

`pixel_adapter.py` submits a real Pixel task through the dashboard API. Use
synthetic tasks only. Supply the dashboard key through a private environment
variable, then run with an unused output path:

```sh
python3 tests/performance/pixel_adapter.py --manifest pixel-task.json --output task-receipt.json
python3 -m unittest discover -s tests/performance -p 'test_pixel_adapter.py' -v
```

Example private execution manifest (port 9 is a placeholder):

```json
{
  "schema_version": 1,
  "base_url": "http://127.0.0.1:9",
  "api_key_env": "ODS_PERF_DASHBOARD_KEY",
  "timeout_s": 60,
  "recovery_timeout_s": 40,
  "task": {
    "id": "synthetic-arithmetic",
    "messages": [{"role": "user", "content": "Use the protected read-only execution tool to calculate 12 * 37 + 10. Do not change any files. Return only the integer."}],
    "validator": {"kind": "exact", "expected": "454"},
    "required_tools": ["pixel_ods_readonly_exec"]
  }
}
```

The CLI saves chat/request IDs before submitting once. It reconciles the exact
retained result after transport completion or failure; observer failure never
triggers a second generation. Unresolved attempts receive one exact cancellation
attempt and bounded readback. Recovery shares one additional time budget.
Connected transport deadlines include trickle headers and bodies; OS DNS remains
outside that bound. The Python `run` caller must persist supplied IDs before
dispatch if it needs recovery after its own process exits.

The receipt hashes the answer and task instead of publishing their text. It
omits authentication selectors, values, endpoint URLs and server error bodies.
Task latency runs from API submission to observing a complete retained receipt.
A correct answer recovered after the deadline remains censored and unsuccessful.
Stream content is never concatenated with the retained answer.

`answer_quality_pass` grades exact text or strict JSON. `quality_pass` additionally
requires any declared tool execution to be proven. Without a native witness,
declaring `required_tools` keeps `quality_pass` and `success` false even when a
tool request and correct answer are observed.

On the serving host, add `--native-sessions-dir /absolute/path/to/agents/pixel/sessions`
to join native execution evidence. The read-only witness verifies that the exact
chat's native session was absent before submission by default, then binds the session index,
single user message, retained final-answer hash, ordered tool-call/result pairs,
and model usage receipts. Failed command results cannot prove successful tool
execution. It independently regrades the native answer; an incorrect answer
remains a bound failure. Ambiguous identities,
stale or malformed transcripts, unsafe paths and changing files remain unproven.
Each submission contains one user message.

Command receipts are recognized for `pixel_ods_readonly_exec` and the
foreground `exec` tool. For `exec`, the tool result must have `isError`
exactly `false`, a `details` object with `status` equal to `completed`, an
integer `exitCode` equal to `0` (booleans and floats are rejected), and a
string `aggregated` field. Any `error` key, or any background/session
identifier such as `sessionId`, `background`, or `jobId`, makes the receipt
unproven. `exec` is not added to the permissive generic-tool set. A
completed foreground command does not certify filesystem side effects or
descendant cleanup; `owned_cleanup_proven` remains null.

An external Python coordinator can reuse a supplied `chat_id` with a new
`request_id` and `native_allow_continuation=True`. This requires
`native_sessions_dir` and explicitly permits an existing session. Before dispatch,
the witness captures its canonical owner-safe transcript and requires its last
message to be a completed textual assistant stop. At completion it requires the
same session and file identity, byte-identical prior history, and complete new
records. Only the appended turn supplies answer, tool and usage evidence; prior
tool calls cannot satisfy the new task. Rewrites, truncation, rotation, an extra
user turn or assistant continuation before the new user message remain unproven.
The coordinator must establish runtime idleness before dispatch: a transcript
stop alone does not prove idle. Persist IDs before every dispatch and never
replay a request just because its observer failed. The CLI continues to create
fresh chats. This option verifies a continuation; it does not grade history
fidelity unless the frozen task's answer oracle tests that behavior.

A model-level text stop can precede an internal assistant continuation when ODS
repairs a missing tool call. Such a stop is recorded in `interim_stops` with its
hash, length, and model-call index. It is accepted only when it has nonempty text,
no calls or pending results, and the next message is an assistant. A second user
message within the measured turn remains unsupported. The last assistant stop must still match the exact
retained API answer, and every model call remains in usage accounting. Load
summaries separately count successful tasks with interim stops. This proves the
terminal result; whether interim text was visible in the browser needs UI testing.

These are operator-observed host records, not protection against a compromised
host. Only declared tool names and hashes/counts are public; transcript text,
arguments, outputs and paths are omitted. Model-call count is populated only
when every observed assistant call has valid completed usage. Missing usage
stays unknown. Underlying model identity and owned-process cleanup remain null.
Successful tool return alone does not validate generated file contents; coding
and artifact tasks still need independent fixture oracles.

Native preflight and observation overhead are recorded separately from API task
latency. `worker_elapsed_ms` includes this overhead and recovery. Measure the
witness's cost before using it in a throughput comparison.

`admission_safe` means the chat activity endpoint proved it terminal, not that
its owned processes were removed. Stop new work when this field is false. Keep
resource monitoring and stop rules in an external coordinator. This is a single
task adapter, not an agent load scheduler or capacity qualification.

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


## Seeded Poisson arrival process (exploratory)

### Real-agent fixed/Poisson arrivals

`pixel_open_loop.py` applies the same bounded arrival schedules to
`pixel_adapter.run`, including retained results, exact-request recovery, and the
optional native tool witness. Its manifest has `schema_version`, `base_url`,
`api_key_env`, `timeout_s`, `recovery_timeout_s`, `tasks`, `concurrency`,
`repetitions`, and `schedule_seed`. Each task uses the single-task adapter's
`id`, `messages`, `validator`, and optional `required_tools`; each must contain
exactly one fresh user message. Task IDs must be unique. Bounds are 100 tasks,
100 repetitions, 1,000 planned arrivals, concurrency 1–8, and a 600-second arrival
span. `--load-config` accepts the fixed/Poisson configurations below.

```bash
python3 pixel_open_loop.py --manifest agent-load.json --load-config load.json \
  --out new-agent-run --native-sessions-dir /private/native/sessions
```

The credential environment variable must already be populated; never put its
value in a manifest. A new output directory is required. `plan.json` persists
every fresh chat/request identity before dispatch; `dispatches.jsonl` records
dispatch intent before a worker can invoke the adapter. `requests.jsonl` has
one terminal record per planned arrival, with the adapter receipt nested inside.
Intent alone does not prove HTTP submission. On observer termination, use these
exact identities for recovery; never automatically resubmit uncertain work.

The generator has no client request queue. Busy or late arrivals are explicit
refusals, including a second lag check after worker admission and receipt-file
sync. `client_busy` is a load-generator limit, not server rejection. Unsafe or
unknown results latch admission closed; a wrong answer with proven idle state
remains a quality failure and does not stop the next scheduled request. A Python
`admission_allowed` callback can stop new work for resource limits. The callback
must be quick and thread-safe. Already-authorized workers drain; an external
resource coordinator must cancel in-flight work using exact persisted identities.
Interrupts drain workers and preserve their outcomes before raising. Receipt-write
failures mark evidence incomplete and fail the run.

`planned` includes the entire schedule; `offered` excludes future arrivals after
the stop latch. Both denominators remain visible. Goodput counts correct,
uncensored, successfully submitted tasks over the entire elapsed time, including
drain and native observation. Arrival-to-completion includes observer work; the
nested adapter latency measures its API boundary separately. p95 remains null
below 100 successful observations. The clock is `perf_counter`, avoiding the
coarse Windows clock used by some Python versions for `monotonic`.

CLI exit 0 means all planned tasks succeeded, 1 means a complete study with
failures/refusals, 2 means invalid input or incomplete evidence, and 130 means an
interrupt. Python-only `request_runner` injection is for synthetic fixtures and
is unavailable in manifests or the CLI. Neither fixture success nor a short live
pilot qualifies serving capacity, model quality, browser latency, or process cleanup.

The open-loop adapter supports an optional Poisson arrival process in addition to the default fixed schedule. The adapter measures offered load; it does not itself qualify capacity.

### Load config examples

Fixed (default, unchanged):

```json
{"rate_rps": 5.0, "max_schedule_lag_ms": 100}
```

Poisson with explicit seed:

```json
{"rate_rps": 5.0, "max_schedule_lag_ms": 100, "arrival_process": "poisson", "arrival_seed": 0}
```

`arrival_seed` must be an integer in `0..2**32-1`. It is required for `poisson` and rejected for `fixed`. Unknown `arrival_process` values are rejected.

### Semantics

- `rate_rps` is the nominal Poisson intensity, not the observed realized rate and not a capacity claim.
- Offsets are cumulative exponential gaps drawn from an isolated `random.Random(seed).expovariate(rate_rps)`. The first arrival is also an exponential gap; there is no forced arrival at t=0.
- Task/repetition ordering comes from the existing deterministic schedule builder. The manifest `schedule_seed` controls task order independently of `arrival_seed`, which controls arrival gaps. Request UUIDs are deliberately unique between runs.
- The full plan of N draws is generated first. If the realized span exceeds 600 seconds, the whole config is rejected before any output directory is created. The schedule is never truncated, rescaled, resampled, or dropped to fit the bound.
- The 600-second cap is checked on the unrounded span, so a value just over the boundary (e.g. 600.0000001 s) is rejected before rounding.
- Client-side admission refusals (`client_busy`, `scheduler_late`, `admission_stop`, `admission_error`) are not server overload signals. They are recorded per planned arrival so all planned requests are accounted for.
- The isolated RNG does not alter the module-level `random` state.

### Scope

Capacity confirmation additionally requires the study's balanced quality gates and at least three independent windows of one minute or longer at the tested load, with resource gates and no unaccounted arrivals. These conditions are necessary, not a substitute for the full protocol. A short Poisson run alone cannot establish production capacity.

The native receipt witness also supports a fresh installation whose session tree
or index has not yet been created. It records an owned existing ancestor and
requires the runtime to create real, owned directories before joining the first
transcript. It never creates session files itself. Continuations still require an
existing store, and prompt, timestamp, tool, usage, and terminal-answer binding
remain required. A successful first-chat proof does not qualify model quality or
serving capacity.

## macOS process memory attribution

The host sampler can optionally record memory for up to eight explicitly
selected PIDs. Discover the engine and VM PIDs separately, then run:

```bash
python3 telemetry.py --output resources.jsonl --duration 60 --interval 1 --scope host \
  --process engine=12345 --process docker_vm=23456
```

Each sample adds a `processes` object, keyed by the supplied labels. It reports
`resident_bytes`, `physical_footprint_bytes`, and `wired_bytes` using macOS's
read-only `proc_pid_rusage` API. These are overlapping accounting views: do
not add them together or sum them across processes to estimate available host
RAM. They do not measure Metal allocations. Align `utc_iso` with request events
and retain host pressure and swap measurements alongside these observations.
The sampler overhead includes the process queries.

The library interface is `MacProcessSampler({"engine": pid}).sample()`. It pins
process identity at construction, using the kernel start counter and executable
UUID internally. `start_abstime` is an identity counter, not a wall-clock date.
Missing, exited, inaccessible, or replaced processes produce null memory fields
and a fixed error code; the sampler never rebinds a PID. Start a new sampler for
an intentionally restarted engine. Permission errors are missing evidence, not
a reason to elevate privileges. UUIDs, command lines, environment variables,
and process names are not emitted. Without `--process`, output is unchanged;
the option is restricted to macOS host scope.

ABI references: Apple's [resource.h](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/resource.h)
and [libproc implementation](https://github.com/apple-oss-distributions/xnu/blob/main/libsyscall/wrappers/libproc/libproc.c).
