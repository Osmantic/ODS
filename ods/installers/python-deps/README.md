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

This pass covers host-agent setup and compose-resolver recovery. Separate
Hugging Face bootstrap installs in Linux phase 11 and the macOS/Windows download
UI helpers, the `pre-download.sh` utility, and Linux's optional mDNS Python
fallback still need complete locked inputs. Their existing package downloads
are not covered by these locks; PB-018 remains partially addressed.

Public PyPI resolution on 2026-09-23 used uv 0.12.18 in a temporary directory:

```sh
uv --no-config pip compile ods/installers/python-deps/host-agent.in --default-index https://pypi.org/simple --universal --python-version 3.8 --generate-hashes --no-header --output-file ods/installers/python-deps/host-agent.txt
uv --no-config pip compile ods/installers/python-deps/pyyaml.in --default-index https://pypi.org/simple --universal --python-version 3.8 --generate-hashes --no-header --output-file ods/installers/python-deps/pyyaml.txt
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
