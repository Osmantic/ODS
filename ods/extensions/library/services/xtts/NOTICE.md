# xtts: upstream provenance notice

The xtts-api-server source is MIT, Coqui TTS runtime source is MPL-2.0, and XTTS-v2 weights use the non-commercial Coqui Public Model License. The recipe now requires an explicit COQUI_TOS_AGREED=1 and overrides the image command that automatically answered yes.

Reviewed on 2026-09-23. [Source revision](https://github.com/daswer123/xtts-api-server/tree/5e8bc93d674fed0f5849e03db28f5e5216320d99); [license evidence](https://github.com/daswer123/xtts-api-server/blob/5e8bc93d674fed0f5849e03db28f5e5216320d99/LICENSE). Scope: application_source_only; excludes container base, dependencies, third-party assets and model weights.

The [machine-readable record](upstream.json) contains the configured image references, reviewed revisions, document hashes, separate components and unresolved gaps. This ODS notice is an attribution/provenance summary, not a replacement for upstream license texts or a grant of rights.

Model evidence: [XTTS-v2](https://huggingface.co/coqui/XTTS-v2/tree/6c2b0d75eae4b7047358e3b6bd9325f857d43f77). Application licenses do not substitute for these model terms.

[XTTS-v2 CPML text](https://huggingface.co/coqui/XTTS-v2/blob/6c2b0d75eae4b7047358e3b6bd9325f857d43f77/LICENSE.txt) was retrieved from the official model repository. Review it and set `COQUI_TOS_AGREED=1` explicitly only if you agree. ODS does not answer an upstream acceptance prompt for you.
