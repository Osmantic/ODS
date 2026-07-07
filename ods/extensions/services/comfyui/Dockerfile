# =============================================================================
# ComfyUI Production Image — SDXL Lightning + NVFP4 Acceleration
#
# Layers ordered for optimal Docker cache:
#   1. System deps (rarely changes)
#   2. PyTorch + CUDA (rarely changes)
#   3. ComfyUI from source
#   4. ComfyUI-Manager
#   5. Custom nodes (each = own RUN for granular caching)
#   6. comfy-kitchen NVFP4 acceleration
#   7. Startup script + non-root user (changes most often)
# =============================================================================

FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    COMFYUI_DIR=/opt/comfyui

# ---------------------------------------------------------------------------
# Layer 1: System dependencies
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv python3-dev \
        git wget ffmpeg libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Layer 2: PyTorch + CUDA 12.8 (Blackwell sm_120 support)
# ---------------------------------------------------------------------------
RUN pip3 install --no-cache-dir \
        torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cu128

# ---------------------------------------------------------------------------
# Layer 3: ComfyUI from source (pinned to v0.16.4)
# ---------------------------------------------------------------------------
RUN git clone --branch v0.16.4 --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFYUI_DIR" \
    && cd "$COMFYUI_DIR" \
    && pip3 install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Layer 4: ComfyUI-Manager
# ---------------------------------------------------------------------------
RUN cd "$COMFYUI_DIR/custom_nodes" \
    && git clone --branch 4.1b2 --depth 1 https://github.com/ltdrdata/ComfyUI-Manager.git \
    && cd ComfyUI-Manager \
    && pip3 install --no-cache-dir -r requirements.txt 2>/dev/null || true

# ---------------------------------------------------------------------------
# Layer 5a: ComfyUI-GGUF (GGUF model loading)
# ---------------------------------------------------------------------------
RUN cd "$COMFYUI_DIR/custom_nodes" \
    && git clone https://github.com/city96/ComfyUI-GGUF.git \
    && cd ComfyUI-GGUF \
    && git checkout 6ea2651e \
    && pip3 install --no-cache-dir -r requirements.txt 2>/dev/null || true

# ---------------------------------------------------------------------------
# Layer 5b: ComfyUI-KJNodes (LTX-2 node graphs)
# ---------------------------------------------------------------------------
RUN cd "$COMFYUI_DIR/custom_nodes" \
    && git clone https://github.com/kijai/ComfyUI-KJNodes.git \
    && cd ComfyUI-KJNodes \
    && git checkout 2747d76e \
    && pip3 install --no-cache-dir -r requirements.txt 2>/dev/null || true

# ---------------------------------------------------------------------------
# Layer 5c: ComfyUI-VideoHelperSuite (video output)
# ---------------------------------------------------------------------------
RUN cd "$COMFYUI_DIR/custom_nodes" \
    && git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git \
    && cd ComfyUI-VideoHelperSuite \
    && git checkout 993082e4 \
    && pip3 install --no-cache-dir -r requirements.txt 2>/dev/null || true

# ---------------------------------------------------------------------------
# Layer 5d: ComfyUI-LTXVideo (Lightricks nodes)
# ---------------------------------------------------------------------------
RUN cd "$COMFYUI_DIR/custom_nodes" \
    && git clone https://github.com/Lightricks/ComfyUI-LTXVideo.git \
    && cd ComfyUI-LTXVideo \
    && git checkout 531512f7 \
    && pip3 install --no-cache-dir -r requirements.txt 2>/dev/null || true

# ---------------------------------------------------------------------------
# Layer 6: comfy-kitchen — NVIDIA NVFP4 acceleration
# ---------------------------------------------------------------------------
RUN pip3 install --no-cache-dir "comfy-kitchen[cublas]"

# ---------------------------------------------------------------------------
# Layer 7: Startup script + non-root user
# ---------------------------------------------------------------------------
RUN useradd -m -u 1000 -s /bin/bash comfyui \
    && chown -R comfyui:comfyui "$COMFYUI_DIR"

COPY --chown=comfyui:comfyui startup.sh /opt/startup.sh
RUN sed -i 's/\r$//' /opt/startup.sh && chmod +x /opt/startup.sh

USER comfyui
WORKDIR $COMFYUI_DIR

EXPOSE 8188

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD wget --spider --quiet http://localhost:8188 || exit 1

ENTRYPOINT ["/opt/startup.sh"]
