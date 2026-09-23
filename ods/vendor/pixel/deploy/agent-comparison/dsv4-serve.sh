#!/bin/sh
set -eu

# Exact product-quality DSV4 launcher for the pinned qualification image.
# The controller supplies the measured model directory as argv[1] and binds
# the remaining task/runtime values in its separately hashed launch vector.
[ "$#" -ge 1 ] || exit 64
model_path=$1
shift
[ "$model_path" = "/models/model" ] || exit 64

exec /opt/venv/bin/vllm serve "$model_path" \
  --trust-remote-code \
  --kv-cache-dtype fp8 \
  --block-size 256 \
  --load-format instanttensor \
  --decode-context-parallel-size 1 \
  --max-num-batched-tokens 2112 \
  --max-cudagraph-capture-size 96 \
  --compilation-config '{"cudagraph_mode":"FULL_AND_PIECEWISE","custom_ops":["all"]}' \
  --async-scheduling \
  --no-scheduler-reserve-full-isl \
  --enable-chunked-prefill \
  --tokenizer-mode deepseek_v4 \
  --enable-prompt-tokens-details \
  --enable-force-include-usage \
  --enable-request-id-headers \
  --default-chat-template-kwargs.thinking=true \
  --default-chat-template-kwargs.reasoning_effort=high \
  --enable-flashinfer-autotune \
  --enable-prefix-caching \
  --speculative-config '{"model":"/models/model","method":"dspark","num_speculative_tokens":5,"draft_sample_method":"probabilistic","rejection_sample_method":"standard"}' \
  --attention-backend B12X_MLA_SPARSE \
  --moe-backend b12x \
  --linear-backend b12x \
  --disable-custom-all-reduce \
  --override-generation-config '{"top_p":0.95}' \
  "$@"
