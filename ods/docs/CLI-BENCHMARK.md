# Scriptable chat round-trip checks

Run `ods benchmark --json` (aliases `ods bench --json` and `ods b --json`)
to receive one JSON object on stdout. Progress and failures go to stderr.

```sh
ods benchmark --json > benchmark.json
jq '{probe, durationSeconds}' benchmark.json
```

The version-1 receipt contains `schemaVersion: 1`, `probe: "chat_round_trip"`,
numeric `durationSeconds`, and the returned text in `response`. Quotes,
backslashes, newlines and Unicode in the answer remain valid JSON. Failed
inference exits nonzero and emits no success receipt; check the exit status
before reading the output file. Unknown arguments exit 2. `--help` does not
require a configured installation or contact inference.

Timing uses a monotonic clock and covers the existing CLI chat flow: loading
configuration, probing the loaded model, sending the fixed Hello World prompt,
receiving and parsing the answer, and local process overhead. It is **not**
decode throughput, time to first token, a cold-start guarantee, or a model
quality score. Compare runs on the same host and configuration; unrelated
load and prompt caching can affect latency. The command performs one real
generation and uses the existing chat timeout and routing behavior.

Python 3, Bash 4+, jq and curl are required. The Bash CLI is available on Linux,
WSL and macOS with a modern Bash. This option is not added to the native
PowerShell CLI. Running `ods benchmark` without flags retains the human report.
No benchmark history or settings are written by this command.
