# Host Python download locks

`host-agent.in` retains `huggingface_hub[hf_xet]>=0.27` and includes PyYAML.
`host-agent.txt` fixes its complete dependency graph with SHA-256 hashes and
Python-version markers. `pyyaml.txt` is the smaller compose-resolver lock used
by the macOS installer and Windows host-agent recovery helper.

The resolution floor is Python 3.8, preserving the existing dependency range
and Apple's Python 3.9 path. This is not a new claim that all host-agent features
have been qualified on Python 3.8. The resolver selects Hugging Face Hub 0.36.2
on Python 3.8, 1.8.0 on Python 3.9, and 1.32.0 on newer interpreters. These are
distinct compatible dependency sets, not a lowered requirement to force an
incompatible release onto an older interpreter.

Installers pass `--require-hashes --only-binary=:all:` when they need to download
these dependencies. A platform without a compatible reviewed wheel fails this
step; there is no fallback to unverified source builds. Existing importable
owner-installed dependencies keep the prior reuse behavior. These locks govern
new downloads, and do not attest to the contents of a pre-existing Python
environment. The existing optional failure handling, user-site installs and
macOS private venv remain in place.

The complete `host-agent.txt` graph is also reused by the Linux phase 11
artifact/embedding bootstraps, macOS and Windows download UI fallbacks, and
`scripts/pre-download.sh`. Reusing this graph includes PyYAML, so these paths do
not maintain a second drifting Hub/Xet resolution. `pre-download.sh` calls pip
through its selected Python and no longer passes the removed `resume_download`
argument to modern Hub; the client's automatic resume behavior applies.

`zeroconf.txt` is the complete lock for Linux's optional mDNS pip fallback.
It selects zeroconf 0.136.2 on Python 3.8, 0.148.0 on Python 3.9 and 0.151.3 on
Python 3.10+, with ifaddr and the older interpreter's async-timeout dependency.
The system package manager remains the first choice; this lock governs only
the Python-package fallback, which uses the same `python3` interpreter as the
import probe. Missing locks, incompatible wheels or hash mismatches do not
fall back to an unpinned package name or source build.

These changes close the previously listed extra Python-download gaps, not all
of PB-018. They do not attest to reused owner packages, distro package-manager
content, model weight downloads or other installer supply-chain inputs.

Public PyPI resolution on 2026-09-23 used uv 0.12.18 in a temporary directory:

```sh
uv --no-config pip compile ods/installers/python-deps/host-agent.in --default-index https://pypi.org/simple --universal --python-version 3.8 --generate-hashes --no-header --output-file ods/installers/python-deps/host-agent.txt
uv --no-config pip compile ods/installers/python-deps/pyyaml.in --default-index https://pypi.org/simple --universal --python-version 3.8 --generate-hashes --no-header --output-file ods/installers/python-deps/pyyaml.txt
uv --no-config pip compile ods/installers/python-deps/zeroconf.in --default-index https://pypi.org/simple --universal --python-version 3.8 --generate-hashes --no-header --output-file ods/installers/python-deps/zeroconf.txt
```

Review dependency and hash changes together. `test-python-locks.yml` checks
fresh installations on declared Linux/macOS/Windows fixtures and older Python
versions. The offline regression feeds a modified wheel through the real Windows
recovery helper and verifies that pip rejects it before installation.

Local evidence: fresh temporary Windows Python 3.9/3.11 and WSL/Linux Python 3.12
venvs installed all locked host dependencies, imported YAML/Hub/Xet and passed
`pip check`. CI tooling locks also installed and imported in separate temporary
venvs on both hosts, including constructing/closing the SDK without an API call.
The macOS runtime helper's creation/reuse/failure fixture and Windows YAML recovery
tests passed. Native macOS and a real ODS install remain unexecuted; CI matrix
entries describe required checks, not evidence that those remote runs occurred.

Extra-bootstrap evidence on 2026-09-23: fresh temporary Linux Python 3.12 and
Windows Python 3.9 venvs installed `zeroconf.txt`, imported its public API without
starting an announcer, and passed `pip check`. Python 3.8 is covered by the
universal resolution/markers, but was not executed in this additional smoke.
`tests/test_extra_python_bootstrap_locks.py` loads individual real bootstrap
functions with isolated interpreter/path fixtures and offline wheels. A wheel
modified after its hash was recorded was rejected by actual pip at all six
boundaries: Linux artifact, Linux embeddings, macOS artifact, Windows artifact,
pre-download and mDNS. The approved wheel installs in a temporary target; a
missing lock cannot trigger an unpinned fallback. POSIX tests passed on WSL;
the real PowerShell boundary passed on Windows. No installer, model download,
host package environment or mDNS service was started. Native macOS remains
unexecuted. Existing macOS download-retry and pre-download prompt regressions
also passed.
