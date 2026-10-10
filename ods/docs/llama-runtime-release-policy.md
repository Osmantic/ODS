# llama.cpp runtime release boundaries

ODS pins runtime images by official tag and immutable manifest digest. The
NVIDIA CUDA default uses b11429 (the code tagged as upstream v0.6.0). Its
Qwen3.5 loader separates the ordinary transformer blocks from optional
NextN/MTP blocks. Earlier b9014 treats that extra block as part of the ordinary
model and can fail with a missing `ssm_conv1d.weight` tensor.

This CUDA change does not advance CPU, AMD Vulkan/ROCm, Intel, Apple Docker,
Intel Arc source builds, or native Windows archives. Their release boundaries
remain b9014. Native release tags in the Gemma tier maps also remain b9014;
those tags select native executables, independently of the CUDA image.
Existing explicit per-model catalog image choices remain unchanged.
The existing catalog refusal for Qwen3.8-27B remains conservative: its negative
evidence names b9014, still shipped by other backends, and is not evidence
about b11429. That catalog quantization needs its own qualification.

`tests/contracts/test-llama-cpp-compat.py` names each backend's expected build
in `BACKEND_BUILDS`. A backend upgrade must change that explicit policy and its
immutable artifact pins together. The contract continues to require:

- Tag-plus-digest image references and one digest per tag.
- Matching default references in Compose, installers, tier maps, host-agent
  fallback, and the dependency lock.
- Matching AMD Compose/configuration/lock references.
- Matching Arc source tags and full commit identities.
- Matching Windows release archive hashes and sizes, verified before extraction.

An image-only compatibility trial can use a model's `runtime_profiles` entry
with a backend and host architecture restriction. The host's normal model
activation transaction then owns admission, runtime startup, and rollback.
The profile's `context_length` supplies a default; an explicit user context
selection still takes precedence. Such a trial is separate from changing a
backend's shipped default.

A single model loading on CPU does not qualify a CUDA default. Before shipping
a changed backend, test the actual target runtime through guarded activation
and rollback, existing supported model families, prompt/tool streaming,
reasoning selection, context/cache settings, and enabled speculation settings.
Qualify additional architectures and multi-GPU configurations in their own
lanes. The b9014-to-b11429 jump includes upstream changes beyond the NextN
loader; CLI flag presence alone is insufficient acceptance evidence.
