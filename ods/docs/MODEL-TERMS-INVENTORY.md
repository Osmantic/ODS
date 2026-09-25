# Model terms source inventory

Reviewed 2026-09-24 UTC against the 57 curated model records in ODS source
`1bc5e1cbb24864f1c9efd551e0613202af324e3f`.

This is a documentation inventory of upstream repository metadata, not a license
grant, legal-compatibility decision, or completed per-artifact clearance. A
quantizer's model-card tag can be incomplete or wrong and does not override the
original model license. Each source link identifies the revision requested by
the catalog; mutable refs need a separate immutable artifact receipt.

Only one catalog entry has a top-level `license`, none has a top-level
`license_url`, and one additional entry has nested `source_evidence` license
information. This page does not change that runtime schema or enforce acceptance.
The separate Hugging Face browser does display upstream license metadata; that
does not close the curated catalog's provenance and acceptance gaps.

Before redistribution or restricted use, validate the original author and model
revision, quantizer relationship, full license text, acceptable-use terms,
required notices, and any required acceptance against the actual artifact.
A missing or `other` tag requires review, not an assumption of Apache-2.0.

| Catalog model | Artifact-source metadata | Declared license tag | Original/base model metadata |
| --- | --- | --- | --- |
| `qwen3.5-2b-q4` | [unsloth/Qwen3.5-2B-GGUF @ f6d5376be1ed](https://huggingface.co/api/models/unsloth/Qwen3.5-2B-GGUF/revision/f6d5376be1edb4d416d56da11e5397a961aca8ae) (requested `f6d5376be1ed`) | apache-2.0 | ["Qwen/Qwen3.5-2B"] |
| `jamba-reasoning-3b-q4` | [ai21labs/AI21-Jamba-Reasoning-3B-GGUF @ 462e08a43c3c](https://huggingface.co/api/models/ai21labs/AI21-Jamba-Reasoning-3B-GGUF/revision/462e08a43c3c32f6b8b85f79ff0796e484d7b65a) (requested `462e08a43c3c`) | apache-2.0 | Not declared |
| `phi4-mini-q4` | [unsloth/Phi-4-mini-instruct-GGUF @ 78eb92a46fc3](https://huggingface.co/api/models/unsloth/Phi-4-mini-instruct-GGUF/revision/78eb92a46fc37e6b524df991ed9aca9bc6aa7b80) (requested `main`) | mit | microsoft/Phi-4-mini-instruct |
| `phi3.5-mini-q4` | [bartowski/Phi-3.5-mini-instruct-GGUF @ 6d70da17e749](https://huggingface.co/api/models/bartowski/Phi-3.5-mini-instruct-GGUF/revision/6d70da17e749a471ccb62ade694486011a75cda3) (requested `main`) | mit | microsoft/Phi-3.5-mini-instruct |
| `phi4-mini-reasoning-q4` | [SandLogicTechnologies/Phi-4-mini-reasoning-GGUF @ 40e6071714bc](https://huggingface.co/api/models/SandLogicTechnologies/Phi-4-mini-reasoning-GGUF/revision/40e6071714bc133bd46ad2dd62eaca1b288219a1) (requested `main`) | mit | ["microsoft/Phi-4-mini-reasoning"] |
| `qwen2.5-1.5b-instruct-q4` | [Qwen/Qwen2.5-1.5B-Instruct-GGUF @ 91cad51170dc](https://huggingface.co/api/models/Qwen/Qwen2.5-1.5B-Instruct-GGUF/revision/91cad51170dc346986eccefdc2dd33a9da36ead9) (requested `main`) | apache-2.0 | Qwen/Qwen2.5-1.5B-Instruct |
| `qwen2.5-0.5b-instruct-q4` | [Qwen/Qwen2.5-0.5B-Instruct-GGUF @ 9217f5db79a2](https://huggingface.co/api/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF/revision/9217f5db79a29953eb74d5343926648285ec7e67) (requested `main`) | apache-2.0 | Qwen/Qwen2.5-0.5B-Instruct |
| `granite3.3-2b-instruct-q4` | [unsloth/granite-3.3-2b-instruct-GGUF @ 8137ae9f7dd3](https://huggingface.co/api/models/unsloth/granite-3.3-2b-instruct-GGUF/revision/8137ae9f7dd30033d76472c87aa8d41c72cb7c10) (requested `main`) | Not declared | Not declared |
| `granite4.0-h-micro-q4` | [ibm-granite/granite-4.0-h-micro-GGUF @ dc1dd2585fac](https://huggingface.co/api/models/ibm-granite/granite-4.0-h-micro-GGUF/revision/dc1dd2585fac18a78001c677d33ef8a7bbb7eb68) (requested `main`) | apache-2.0 | ["ibm-granite/granite-4.0-h-micro"] |
| `granite4.0-h-tiny-q4` | [ibm-granite/granite-4.0-h-tiny-GGUF @ 08d5a8a9741d](https://huggingface.co/api/models/ibm-granite/granite-4.0-h-tiny-GGUF/revision/08d5a8a9741dd5c1a95d2d39e25253226aa1464e) (requested `main`) | apache-2.0 | ["ibm-granite/granite-4.0-h-tiny"] |
| `smollm3-3b-q4` | [unsloth/SmolLM3-3B-GGUF @ a7bc17204c8a](https://huggingface.co/api/models/unsloth/SmolLM3-3B-GGUF/revision/a7bc17204c8a326d6bd6e466e076959eddae2025) (requested `main`) | apache-2.0 | ["HuggingFaceTB/SmolLM3-3B"] |
| `gemma3-4b-it-q4` | [ggml-org/gemma-3-4b-it-GGUF @ d09762237476](https://huggingface.co/api/models/ggml-org/gemma-3-4b-it-GGUF/revision/d0976223747697cb51e056d85c532013931fe52e) (requested `main`) | gemma | ["google/gemma-3-4b-it"] |
| `granite4.0-h-1b-q4` | [ibm-granite/granite-4.0-h-1b-GGUF @ c2cb1972f511](https://huggingface.co/api/models/ibm-granite/granite-4.0-h-1b-GGUF/revision/c2cb1972f511add21f3bae244990b8ff3a3ffb23) (requested `main`) | apache-2.0 | ["ibm-granite/granite-4.0-h-1b"] |
| `falcon-h1-1.5b-instruct-q4` | [tiiuae/Falcon-H1-1.5B-Instruct-GGUF @ 0d3a6cfe25fb](https://huggingface.co/api/models/tiiuae/Falcon-H1-1.5B-Instruct-GGUF/revision/0d3a6cfe25fb4eeab0153fb8623aac5b69d6bd0a) (requested `main`) | other | tiiuae/Falcon-H1-1.5B-Instruct |
| `falcon-h1-3b-instruct-q4` | [tiiuae/Falcon-H1-3B-Instruct-GGUF @ 18cc9812739f](https://huggingface.co/api/models/tiiuae/Falcon-H1-3B-Instruct-GGUF/revision/18cc9812739f6040f795ddf9d92c9da9a8551572) (requested `main`) | other | tiiuae/Falcon-H1-3B-Base |
| `nvidia-nemotron3-nano-4b-q4` | [nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF @ ba223d14e455](https://huggingface.co/api/models/nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF/revision/ba223d14e45525f7fae81db77ea8cabeb2fc6c25) (requested `main`) | other | nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8 |
| `granite4.1-3b-q4` | [ibm-granite/granite-4.1-3b-GGUF @ ab4701481089](https://huggingface.co/api/models/ibm-granite/granite-4.1-3b-GGUF/revision/ab4701481089b58a082ef63cc1cee738887293ff) (requested `main`) | apache-2.0 | ["ibm-granite/granite-4.1-3b"] |
| `granite4.0-1b-q4` | [ibm-granite/granite-4.0-1b-GGUF @ b27c2fe3f211](https://huggingface.co/api/models/ibm-granite/granite-4.0-1b-GGUF/revision/b27c2fe3f211b7f44e80fa620177aea371099aaa) (requested `main`) | apache-2.0 | ["ibm-granite/granite-4.0-1b"] |
| `granite4.0-h-350m-q4` | [ibm-granite/granite-4.0-h-350m-GGUF @ a864f823cce6](https://huggingface.co/api/models/ibm-granite/granite-4.0-h-350m-GGUF/revision/a864f823cce6e6048b5752e2816fe7a23987d790) (requested `main`) | apache-2.0 | ["ibm-granite/granite-4.0-h-350m"] |
| `granite3.2-2b-instruct-q4` | [ibm-research/granite-3.2-2b-instruct-GGUF @ 153d944aab8c](https://huggingface.co/api/models/ibm-research/granite-3.2-2b-instruct-GGUF/revision/153d944aab8ce9c56ddcedbafc1a682341651f11) (requested `main`) | apache-2.0 | ["ibm-granite/granite-3.2-2b-instruct"] |
| `granite3.1-2b-instruct-q4` | [bartowski/granite-3.1-2b-instruct-GGUF @ e47b8b46c04c](https://huggingface.co/api/models/bartowski/granite-3.1-2b-instruct-GGUF/revision/e47b8b46c04cede00f9e19d5a846551b14b2efce) (requested `main`) | apache-2.0 | ibm-granite/granite-3.1-2b-instruct |
| `phi3-mini-128k-q4` | [QuantFactory/Phi-3-mini-128k-instruct-GGUF @ 0cc851bb3014](https://huggingface.co/api/models/QuantFactory/Phi-3-mini-128k-instruct-GGUF/revision/0cc851bb3014ecd709dbe418684c8d66baa35263) (requested `main`) | mit | Not declared |
| `ministral3-8b-instruct-2512-q4` | [mistralai/Ministral-3-8B-Instruct-2512-GGUF @ 0102285ad796](https://huggingface.co/api/models/mistralai/Ministral-3-8B-Instruct-2512-GGUF/revision/0102285ad796bd99af90f58de616092e5630e970) (requested `0102285ad796`) | apache-2.0 | ["mistralai/Ministral-3-8B-Instruct-2512"] |
| `ministral-3b-instruct-q4` | [mradermacher/Ministral-3b-instruct-GGUF @ e31dc5d6827b](https://huggingface.co/api/models/mradermacher/Ministral-3b-instruct-GGUF/revision/e31dc5d6827bc82ebf9525f1b6ee9cb33f0ab6a6) (requested `main`) | apache-2.0 | ministral/Ministral-3b-instruct |
| `llama3.2-1b-instruct-q4` | [unsloth/Llama-3.2-1B-Instruct-GGUF @ b69aef112e9f](https://huggingface.co/api/models/unsloth/Llama-3.2-1B-Instruct-GGUF/revision/b69aef112e9f895e6f98d7ae0949f72ff09aa401) (requested `main`) | llama3.2 | meta-llama/Llama-3.2-1B-Instruct |
| `llama3.2-3b-instruct-q4` | [hugging-quants/Llama-3.2-3B-Instruct-Q4_K_M-GGUF @ eb72f2a08dd2](https://huggingface.co/api/models/hugging-quants/Llama-3.2-3B-Instruct-Q4_K_M-GGUF/revision/eb72f2a08dd2b9edd07ffacfe5aa56938b7939b0) (requested `main`) | Not declared | meta-llama/Llama-3.2-3B-Instruct |
| `qwen2.5-3b-instruct-q4` | [Qwen/Qwen2.5-3B-Instruct-GGUF @ 7dabda4d13d5](https://huggingface.co/api/models/Qwen/Qwen2.5-3B-Instruct-GGUF/revision/7dabda4d13d513e3e842b20f0d435c732f172cbe) (requested `main`) | other | Qwen/Qwen2.5-3B-Instruct |
| `qwen3-4b-q4` | [Qwen/Qwen3-4B-GGUF @ bc640142c66e](https://huggingface.co/api/models/Qwen/Qwen3-4B-GGUF/revision/bc640142c66e1fdd12af0bd68f40445458f3869b) (requested `main`) | apache-2.0 | Qwen/Qwen3-4B |
| `qwen3-4b-instruct-2507-q4` | [unsloth/Qwen3-4B-Instruct-2507-GGUF @ a06e946bb6b6](https://huggingface.co/api/models/unsloth/Qwen3-4B-Instruct-2507-GGUF/revision/a06e946bb6b655725eafa393f4a9745d460374c9) (requested `main`) | apache-2.0 | ["Qwen/Qwen3-4B-Instruct-2507"] |
| `qwen3-4b-128k-q4` | [unsloth/Qwen3-4B-128K-GGUF @ a21842b9582c](https://huggingface.co/api/models/unsloth/Qwen3-4B-128K-GGUF/revision/a21842b9582c3888b30568b8e84afe533275f646) (requested `main`) | apache-2.0 | Qwen/Qwen3-4B |
| `qwen3-1.7b-q4` | [ggml-org/Qwen3-1.7B-GGUF @ daeb8e2d528a](https://huggingface.co/api/models/ggml-org/Qwen3-1.7B-GGUF/revision/daeb8e2d528a760970442092f6bf1e55c3b659eb) (requested `main`) | apache-2.0 | Qwen/Qwen3-1.7B |
| `qwen2.5-coder-1.5b-128k-q4` | [unsloth/Qwen2.5-Coder-1.5B-Instruct-128K-GGUF @ 15e05dbeacf9](https://huggingface.co/api/models/unsloth/Qwen2.5-Coder-1.5B-Instruct-128K-GGUF/revision/15e05dbeacf9e5dc519ee1ac64308465cee5db77) (requested `main`) | apache-2.0 | Qwen/Qwen2.5-Coder-1.5B-Instruct |
| `qwen2.5-coder-3b-128k-q4` | [unsloth/Qwen2.5-Coder-3B-Instruct-128K-GGUF @ 5326551926d0](https://huggingface.co/api/models/unsloth/Qwen2.5-Coder-3B-Instruct-128K-GGUF/revision/5326551926d06f7f9cab53c9b9b552e3bedfe8ba) (requested `main`) | apache-2.0 | Qwen/Qwen2.5-Coder-3B-Instruct |
| `qwen2.5-7b-instruct-q4` | No direct GGUF source | No direct Hugging Face GGUF source in this record | Not declared |
| `llama3.1-8b-instruct-q4` | [bartowski/Meta-Llama-3.1-8B-Instruct-GGUF @ bf5b95e96dac](https://huggingface.co/api/models/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/revision/bf5b95e96dac0462e2a09145ec66cae9a3f12067) (requested `main`) | llama3.1 | meta-llama/Meta-Llama-3.1-8B-Instruct |
| `granite3.3-8b-instruct-q4` | [ibm-granite/granite-3.3-8b-instruct-GGUF @ e40e9dd739c7](https://huggingface.co/api/models/ibm-granite/granite-3.3-8b-instruct-GGUF/revision/e40e9dd739c7be00fa965c16ce167088190ce114) (requested `main`) | apache-2.0 | ["ibm-granite/granite-3.3-8b-instruct"] |
| `mistral-nemo-12b-instruct-q4` | [QuantFactory/Mistral-Nemo-Instruct-2407-GGUF @ 6d01a4babf90](https://huggingface.co/api/models/QuantFactory/Mistral-Nemo-Instruct-2407-GGUF/revision/6d01a4babf90e5b1f274c8f37867a6b0494cdf5a) (requested `main`) | apache-2.0 | Not declared |
| `qwen3.5-4b-q4` | [unsloth/Qwen3.5-4B-GGUF @ e87f176479d0](https://huggingface.co/api/models/unsloth/Qwen3.5-4B-GGUF/revision/e87f176479d0855a907a41277aca2f8ee7a09523) (requested `main`) | apache-2.0 | ["Qwen/Qwen3.5-4B"] |
| `gemma4-e2b-q4` | [unsloth/gemma-4-E2B-it-GGUF @ 0314792d7f1f](https://huggingface.co/api/models/unsloth/gemma-4-E2B-it-GGUF/revision/0314792d7f1f7e229411f620751375812bb9faf2) (requested `0314792d7f1f`) | apache-2.0 | google/gemma-4-E2B-it |
| `deepseek-r1-7b-q4` | [unsloth/DeepSeek-R1-Distill-Qwen-7B-GGUF @ 097680e4eed7](https://huggingface.co/api/models/unsloth/DeepSeek-R1-Distill-Qwen-7B-GGUF/revision/097680e4eed7a83b3df6b0bb5e5134099cadf1b0) (requested `main`) | apache-2.0 | deepseek-ai/DeepSeek-R1-Distill-Qwen-7B |
| `gemma4-e4b-q4` | [unsloth/gemma-4-E4B-it-GGUF @ bfc15c382204](https://huggingface.co/api/models/unsloth/gemma-4-E4B-it-GGUF/revision/bfc15c382204943c3a8fff0c750b94ae2364d7a3) (requested `bfc15c382204`) | apache-2.0 | google/gemma-4-E4B-it |
| `qwen3.5-9b-q4` | [unsloth/Qwen3.5-9B-GGUF @ 3885219b6810](https://huggingface.co/api/models/unsloth/Qwen3.5-9B-GGUF/revision/3885219b6810b007914f3a7950a8d1b469d598a5) (requested `main`) | apache-2.0 | ["Qwen/Qwen3.5-9B"] |
| `phi4-q4` | [bartowski/phi-4-GGUF @ 19cd65f97c2f](https://huggingface.co/api/models/bartowski/phi-4-GGUF/revision/19cd65f97c2f1712a81c506611d3f9c94b16a1e1) (requested `main`) | mit | microsoft/phi-4 |
| `deepseek-r1-14b-q4` | [unsloth/DeepSeek-R1-Distill-Qwen-14B-GGUF @ 7b05b58b41f6](https://huggingface.co/api/models/unsloth/DeepSeek-R1-Distill-Qwen-14B-GGUF/revision/7b05b58b41f623e66fc74cd27b35475267b2f3e3) (requested `main`) | apache-2.0 | deepseek-ai/DeepSeek-R1-Distill-Qwen-14B |
| `qwen3.8-27b-iq4-xs` | [unsloth/Qwen3.8-27B-GGUF @ 4ca720788d1e](https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF/revision/4ca720788d1e01f1bff70c033e0d0028fd02e502) (requested `4ca720788d1e`) | apache-2.0 | ["Qwen/Qwen3.8-27B"] |
| `qwen3.5-27b-q4` | [unsloth/Qwen3.5-27B-GGUF @ 3221f178a6b8](https://huggingface.co/api/models/unsloth/Qwen3.5-27B-GGUF/revision/3221f178a6b842d04f1fb42f1c413534adcc0a6a) (requested `main`) | apache-2.0 | ["Qwen/Qwen3.5-27B"] |
| `gemma4-26b-a4b-q4` | [ggml-org/gemma-4-26B-A4B-it-GGUF @ bb4531cda34d](https://huggingface.co/api/models/ggml-org/gemma-4-26B-A4B-it-GGUF/revision/bb4531cda34d1ea09d9814959ed4d5833cf2a4c8) (requested `main`) | apache-2.0 | ["google/gemma-4-26B-A4B-it"] |
| `qwen3-30b-a3b-q4` | [unsloth/Qwen3-30B-A3B-GGUF @ d5b1d57bd0b5](https://huggingface.co/api/models/unsloth/Qwen3-30B-A3B-GGUF/revision/d5b1d57bd0b504ac62ae6c725904e96ef228dc74) (requested `main`) | apache-2.0 | Qwen/Qwen3-30B-A3B |
| `gemma4-31b-q4` | [ggml-org/gemma-4-31B-it-GGUF @ 4fa4fdf38bee](https://huggingface.co/api/models/ggml-org/gemma-4-31B-it-GGUF/revision/4fa4fdf38bee237b5c9e8a5b4e72cf39404c9dcc) (requested `main`) | apache-2.0 | ["google/gemma-4-31B-it"] |
| `deepseek-r1-32b-q4` | [unsloth/DeepSeek-R1-Distill-Qwen-32B-GGUF @ 1938d05cc893](https://huggingface.co/api/models/unsloth/DeepSeek-R1-Distill-Qwen-32B-GGUF/revision/1938d05cc893a60f37be1dc16e7465038f4fca63) (requested `main`) | apache-2.0 | deepseek-ai/DeepSeek-R1-Distill-Qwen-32B |
| `qwen3.5-35b-a3b-q4` | [unsloth/Qwen3.5-35B-A3B-GGUF @ bc014a17be43](https://huggingface.co/api/models/unsloth/Qwen3.5-35B-A3B-GGUF/revision/bc014a17be43adabd7066b7a86075ff935c6a4e2) (requested `main`) | apache-2.0 | ["Qwen/Qwen3.5-35B-A3B"] |
| `qwen3.6-35b-a3b-ud-q4` | [unsloth/Qwen3.6-35B-A3B-GGUF @ a483e9e6cbd5](https://huggingface.co/api/models/unsloth/Qwen3.6-35B-A3B-GGUF/revision/a483e9e6cbd595906af30beda3187c2663a1118c) (requested `main`) | apache-2.0 | ["Qwen/Qwen3.6-35B-A3B"] |
| `kat-coder-v2.5-dev-apex-q4` | [mudler/KAT-Coder-V2.5-Dev-APEX-GGUF @ be23ff3a49ee](https://huggingface.co/api/models/mudler/KAT-Coder-V2.5-Dev-APEX-GGUF/revision/be23ff3a49eee0d5160e3fd4f5d58062160856c2) (requested `be23ff3a49ee`) | apache-2.0 | Kwaipilot/KAT-Coder-V2.5-Dev |
| `deepseek-r1-70b-q4` | [unsloth/DeepSeek-R1-Distill-Llama-70B-GGUF @ 732dd974083e](https://huggingface.co/api/models/unsloth/DeepSeek-R1-Distill-Llama-70B-GGUF/revision/732dd974083ea5877d7b6d788b36fe7c2e5eab36) (requested `main`) | llama3.3 | deepseek-ai/DeepSeek-R1-Distill-Llama-70B |
| `qwen3-coder-next-q4` | [unsloth/Qwen3-Coder-Next-GGUF @ ce09c67b53bc](https://huggingface.co/api/models/unsloth/Qwen3-Coder-Next-GGUF/revision/ce09c67b53bc8739eef83fe67b2f5d293c270632) (requested `main`) | apache-2.0 | ["Qwen/Qwen3-Coder-Next"] |
| `llama4-scout-q4` | No direct GGUF source | No direct Hugging Face GGUF source in this record | Not declared |
| `qwen3.5-122b-a10b-q4` | No direct GGUF source | No direct Hugging Face GGUF source in this record | Not declared |

## Maintenance

Rebuild this evidence when an artifact source or revision changes. Keep this
inventory distinct from the runtime catalog until the runtime terms schema and
acceptance flow are separately reviewed and tested. See
[third-party licensing](THIRD-PARTY-LICENSING.md) for remaining work.
