# vLLM Extension

A high-throughput, multi-GPU inference backend utilizing PagedAttention.

## Usage
1. Provide a model repository in `.env`: `VLLM_MODEL=meta-llama/Meta-Llama-3-8B-Instruct`
2. Provide your HuggingFace token if required: `HUGGING_FACE_HUB_TOKEN=hf_...`
3. (Optional) Set tensor parallelism if you have multiple GPUs: `VLLM_TP_SIZE=2`
4. Route Dream Server traffic to vLLM by setting this in `.env`:
   `LLM_API_URL=http://vllm:8000/v1`
