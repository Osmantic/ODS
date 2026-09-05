# Pixel model capability qualification

ODS does not infer Pixel reliability from direct chat, route, load, ODS Talk,
or generic agent evidence. Pixel qualification is a separate catalog verdict
under `app_compatibility.pixel_agent`, but it is a recommendation and evidence
signal rather than an admission gate. Pixel attempts work with every active ODS
chat model. Models without a passing qualification use Pixel's adaptive route so
owners can use, compare, and improve them instead of being locked out.

This does not weaken Pixel's safety boundary. Typed tools, broker policy,
approval requirements, bounded execution, cancellation, and receipts are
enforced mechanically for every model. Qualification measures how effectively a
model uses that harness; it does not grant the model additional authority.

## Qualification bar

A model is `verified` for Pixel only when a real Pixel turn on the named host
and runtime does all of the following:

1. Understands a bounded owner request without inventing a different contract.
2. Uses Pixel's real tools to create or modify the requested workspace
   artifacts.
3. Starts the exact requested verification command in the background and polls
   it to a terminal result.
4. Diagnoses and repairs a failure without weakening the owner's assertions or
   destroying unrelated work.
5. Finishes with a concise, accurate owner-facing result and never claims
   success without a terminal zero exit.
6. Survives an independent replay of the requested verification outside the
   model turn.

`not_agent_viable` means a real Pixel turn failed that demanding reliability
bar. `unknown` means no matching host-scoped Pixel qualification exists. Both
remain usable in Pixel and visible in the model selector with adaptive routing
and an evidence-backed quality tier. `verified` models may be recommended by default; the verdict
must never disable chat or make an otherwise callable model inaccessible to
Pixel.

## 2026-08-30 Windows laptop probe

The probe ran through the installed ODS Pixel UI on WSL2 Ubuntu 24.04 with an
NVIDIA 8 GB GPU. The first four candidates used a 64K active context. Qwen3 4B
Instruct 2507 was additionally profiled at 64K, 32K, and 24K because the larger
contexts did not reach a first tool action within the observational budget. The
installed ODS source was `df05a732ed7aedac6c527e1f9e7eeeeccfed3a5b`;
the installed Pixel source was
`f1f811d02bffd5a1589eb6feb34323f6dadf7832`.

Each model received a clean `/workspace/pixel_<model>_probe` directory and the
same task: implement a standard-library TTL/LRU cache plus deterministic
unittests, run exactly `python3 -m unittest -v` as a background command, poll it
to completion, repair failures, and report only verified truth.

### NVIDIA Nemotron 3 Nano 4B

- Pixel session: `c6de656b-4b30-4b94-951f-55e2d6beb16f`
- The model created an implementation and tests but ignored the injected fake
  clock, copied test behavior into implementation details, used real sleeping,
  and did not implement correct LRU/size semantics.
- It ran tests twice, observed failures, emitted a long circular response, and
  made no useful repair or concise honest terminal report.
- Independent replay of `python3 -m unittest -v` ran five tests with two
  failures.
- Verdict: `not_agent_viable` on `windows-laptop` for this runtime.

### Qwen 3.5 4B

- Pixel session: `7f9cb5c6-b64e-41e3-bc2e-ad24013e33a5`
- The model created both files and launched a background unittest command, but
  the suite failed with an undefined `time` name.
- It made one ineffective duplicate edit, reached the bounded tool circuit
  breaker, and ended without a rerun or an honest terminal result.
- Independent replay of `python3 -m unittest -v` still failed with the same
  `NameError`; the implementation also invented a bytes-only contract and had
  incorrect overwrite-at-capacity behavior.
- Verdict: `not_agent_viable` on `windows-laptop` for this runtime.

### Qwen 2.5 Coder 3B 128K

- Pixel session: `0e477566-5158-425d-a54c-666eb0887481`
- The model made no tool calls and created no files.
- It returned malformed mock protocol text labeled as example conversation,
  code, and reply instead of operating Pixel.
- Verdict: `not_agent_viable` on `windows-laptop` for this runtime.

### Ministral 3 8B Instruct 2512

- Pixel session: `f150ff79-c88e-4b12-a564-0769fd52a6a8`
- The real tool-loop generation rate fell to roughly 2.5 tok/s after the
  shallow Models-page activation probe had reported 15.1 tok/s.
- The model wrote the implementation in the requested directory but wrote the
  tests to a misspelled sibling directory. It then ran a different unittest
  command than requested, received an import failure, hallucinated a filename
  correction, emitted an invalid patch, attempted a no-op edit against a
  missing file, and recursively deleted the requested project tree without the
  owner asking for deletion.
- It eventually recreated both requested artifacts and polled the exact
  background command to terminal completion, but seven tests finished with two
  failures: TTL expiry was refreshed incorrectly and `__len__` did not reclaim
  expired entries. Independent replay reproduced the same two failures.
- Its attempted repair duplicated the clock read, did not address expired-entry
  reclamation, and the following model request timed out. Pixel returned to
  `Available` without any owner-facing terminal report.
- No verified passing result was produced.
- Verdict: `not_agent_viable` on `windows-laptop` for this runtime.

### Qwen3 4B Instruct 2507

- Initial Pixel session: `505b13f2-871a-4aaf-a381-34565bd6d617`
- Focused continuation session: `cd025ef5-6217-405c-8b7c-55a91fd29831`
- Direct OpenAI-compatible tool requests proved that the model artifact and
  chat template could emit a valid structured tool call. A roughly 9.7K-token
  direct request also produced a tool call, so model loading alone was not the
  failure.
- At 64K and 32K active context, cold full Pixel turns did not reach a first
  tool action within more than two minutes. At 24K, the cold turn reached its
  first tool action after roughly 110 seconds; 35 of 37 model layers remained
  on GPU and the runtime stayed within the 8 GB device budget.
- The 24K turn created both files and used a background unittest command, but
  ignored the fake-clock/no-sleep contract, validated constructor arguments in
  the wrong method, mishandled updates and lazy reclamation, and wrote a
  self-comparison that could never pass.
- The model invented a process-session alias in the initial turn. Candidate
  guard commit `7c1a8af544dcde07e0104a35804fe653c612835d` repaired only the exact
  `session-<known-label>-<pid>` shape for a process already created by the same
  run. The focused continuation then polled the canonical `fresh-ocean` and
  `dawn-glade` labels successfully, demonstrating that background-process
  continuity was no longer the blocker.
- The continuation inspected the real two-test failure, but retained real
  sleeping, added expiration cleanup after capacity eviction, failed to repair
  the self-comparison, and eventually introduced a duplicate `__len__` method.
  The bounded verification guard stopped the retry loop and the ODS UI
  correctly reported failure instead of exposing a false success.
- Independent replay of `python3 -m unittest -v` still ran seven tests with two
  failures. Source inspection confirmed the remaining duplicate method and
  broader contract defects.
- Verdict: `not_agent_viable` on `windows-laptop` for the tested 24K runtime.

### Qwen 3.5 9B

- Pixel session: `551719d8-dfd9-421b-9da2-d1d3ecf30613`
- A direct OpenAI-compatible request at 24K produced a valid structured `exec`
  call in about 1.2 seconds, and the first full Pixel turn reached a real tool
  action in about 9.5 seconds. This established responsive tool syntax, not a
  successful agent workflow.
- The 24K turn created the requested module and seven-test suite, ran the exact
  unittest command in the background, and polled it to completion. Five tests
  failed, and the final one-token response was stopped for length after the
  accumulated prompt reached the active context envelope. The truth-preserving
  ingress correctly exposed failure instead of the truncated model text.
- ODS then atomically reconfigured the same artifact to its NVIDIA 8 GB 32K
  Q8-KV profile. The model identity, llama.cpp `n_ctx=32768`, OpenClaw
  `contextWindow=32768`, 4,096-token maximum output, sandbox persistence, and
  post-restart service health were all confirmed before continuing.
- The focused 32K continuation read the preserved source, performed two rewrite
  and verification cycles, and polled canonical background-process sessions.
  Its first rewrite caused seven `UnboundLocalError` results. Its second rewrite
  restored execution, but independent exact replay still ran seven tests with
  four failures. The implementation extended TTL on `get`, while the generated
  tests inverted the required `clock() >= deadline` boundary and assumed
  distinct deadlines for keys created at the same fake time.
- The final model call was truncated after its per-turn generation budget, and
  a third evidence-guided continuation was rejected by OpenClaw's context
  precheck before any model or tool action because the preserved chat no longer
  fit in 32K. The UI reported `Context overflow` and recommended a reset or a
  larger-context model.
- Fresh 64K revalidation session
  `b54637ca-feff-42fe-9a0e-d99c607d1438` used the same exact artifact with
  llama.cpp and OpenClaw both confirmed at 65,536 tokens, 4,096 maximum output
  tokens, about 6.0/8.0 GB live GPU use, and a healthy post-activation runtime.
  The first full Pixel turn reached tools and repeatedly ran the required
  background unittest command, but spent six failed verification cycles
  editing fragile `MagicMock.side_effect` sequences instead of correcting the
  required cache semantics. The bounded guard then denied further coding tools,
  and the candidate ingress settled the dashboard back to `Available` with the
  host-authoritative failure message.
- Independent exact replay at the preserved 64K workspace ran seven authored
  tests with two failures. Eight stable-clock hidden tests found two additional
  semantic failures: an expired non-LRU entry survived lazy cleanup, and adding
  a key evicted a live key while that expired entry remained. A focused hidden
  test also proved that updating the most-recent key at capacity wrongly evicted
  the other live key.
- Verdict: `not_agent_viable` on `windows-laptop` for the tested 24K, 32K, and
  64K profiles. The artifact remains suitable for direct chat, but additional
  context did not produce a verified Pixel-agent workflow on this host.

## Revalidation

A model may be promoted only by a later evidence entry that names the exact ODS
and Pixel source revisions, host scope, model artifact, runtime profile, active
context, Pixel session, terminal verification result, and independent replay.
Changing a prompt, plugin, model artifact, runtime profile, or context can
justify revalidation; it does not erase prior scoped failure evidence.

### Qwen 3.5 9B revalidation (2026-09-02)

Qwen 3.5 9B was revalidated through the installed ODS Pixel path on the same
`windows-laptop` WSL2 Ubuntu 24.04 host and NVIDIA 8 GB GPU. The exact model
artifact was `Qwen3.5-9B-Q4_K_M.gguf`
(`03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8`),
using profile `nvidia-8gb-64k-q8-kv` at an active context of 65,536. The
installed ODS source was `d0808d08645841ffcbb3cf3919a9c81fe485937b`; the
installed Pixel 4.3.23 source and harness were
`d99923246e5ea22c0f1c8c8fc7b0927ac8b523fe`.

- Session `80901059-efd1-4df0-b5d9-d09b97164704` created a JSON task-board
  CLI and subprocess-based unittest suite from scratch. It found and fixed its
  own boolean-versus-integer validation defect and a contradictory expected
  result, finished 9/9, and passed an independent exact parsed-output check.
- Session `b8365ace-7fea-4c78-8a23-897f239676db` repaired two seeded log
  rotation defects and finished 8/8, but those commands completed too quickly
  to count as proof of background-process continuity.
- Load-bearing session `0ce4de20-5faf-42b2-ba41-b609b2a5ad28` ran exactly
  `python3 -m unittest -v`. The command yielded process `salty-falcon`; Pixel
  polled it to a terminal four-failure result, diagnosed two root causes, and
  changed only `rotation.py`. The exact rerun yielded process `lucky-crest`;
  Pixel polled it to exit zero with 9/9 in 12.001 seconds, then produced the
  exact `rotation_repair=passed` functional marker.
- Independent replay again passed 9/9 in 12.001 seconds and produced the same
  functional marker. The fixed README and test files remained unchanged at
  SHA-256 `2037f5ef5ed30d30f9b175e0aadabb33037e3d9df76fccb9e2b9d8af975afd7e`
  and `0c409358965594452726de17e62d1c8dadd41b06de1e1d04b56823870b25dd39`;
  the repaired implementation was pinned at
  `392135fb056cdfcf09e82f99f7755a2311975905e069ef3d263d16a4b4f4e309`.
- The ingress returned to `active=false`, `streams=0` after the run.

Verdict: `verified` for Pixel capability on this exact host, artifact, 64K
profile, ODS source, and Pixel source. This is not a cross-profile throughput
claim. The 2026-08-30 failures above remain authoritative historical evidence
for their older ODS/Pixel pair; they are superseded only within this newly
qualified scope and never made the model inaccessible to Pixel.
