# =============================================================================
# ComfyUI Production Image — NVIDIA CUDA 12.8 (x86-64), SDXL Lightning
#
# Layers ordered for optimal Docker cache:
#   1. System deps (rarely changes)
#   2. PyTorch + CUDA (pinned, rarely changes)
#   3. ComfyUI from source (pinned tag)
#   4. Custom nodes (each = own RUN for granular caching, pinned commits)
#   5. comfy-kitchen NVFP4 acceleration
#   6. Build checks: pinned torch kept, every custom node imports
#   7. Startup script + non-root user (changes most often)
#
# ComfyUI-Manager is not installed. Its checkout never loaded as a custom node
# under ComfyUI 0.16.4 (that release expects the comfyui_manager package and
# --enable-manager), and nodes it installed would not survive a recreate.
# =============================================================================

FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04@sha256:b0e5afeabc356a9c41148c50f219d3117d46cb6ec23c590e3a0ebaa1db0ff946

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
# Layer 2: PyTorch + CUDA 12.8 (Blackwell sm_120 support), pinned as in
# Dockerfile.standalone. torchaudio comes from the same index, so ComfyUI's
# requirements below find all three already installed.
# ---------------------------------------------------------------------------
RUN pip3 install --no-cache-dir \
        torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 \
        --index-url https://download.pytorch.org/whl/cu128

# ---------------------------------------------------------------------------
# Layer 3: ComfyUI from source (pinned to v0.16.4)
# ---------------------------------------------------------------------------
RUN git clone --branch v0.16.4 --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFYUI_DIR" \
    && cd "$COMFYUI_DIR" \
    && pip3 install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Layer 4a: ComfyUI-GGUF (GGUF model loading)
# ---------------------------------------------------------------------------
RUN cd "$COMFYUI_DIR/custom_nodes" \
    && git clone https://github.com/city96/ComfyUI-GGUF.git \
    && cd ComfyUI-GGUF \
    && git checkout 6ea2651e \
    && pip3 install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Layer 4b: ComfyUI-KJNodes (LTX-2 node graphs)
# ---------------------------------------------------------------------------
RUN cd "$COMFYUI_DIR/custom_nodes" \
    && git clone https://github.com/kijai/ComfyUI-KJNodes.git \
    && cd ComfyUI-KJNodes \
    && git checkout 2747d76e \
    && pip3 install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Layer 4c: ComfyUI-VideoHelperSuite (video output)
# ---------------------------------------------------------------------------
RUN cd "$COMFYUI_DIR/custom_nodes" \
    && git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git \
    && cd ComfyUI-VideoHelperSuite \
    && git checkout 993082e4 \
    && pip3 install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Layer 4d: ComfyUI-LTXVideo (Lightricks nodes)
# ---------------------------------------------------------------------------
RUN cd "$COMFYUI_DIR/custom_nodes" \
    && git clone https://github.com/Lightricks/ComfyUI-LTXVideo.git \
    && cd ComfyUI-LTXVideo \
    && git checkout 531512f7 \
    && pip3 install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Layer 5: comfy-kitchen — NVIDIA NVFP4 acceleration
#
# Its CUDA kernels need a PyTorch built for CUDA 13 (cu130, driver r580+);
# with this cu128 build ComfyUI logs "You need pytorch with cu130 or higher"
# and uses comfy-kitchen's eager backend instead.
# ---------------------------------------------------------------------------
RUN pip3 install --no-cache-dir "comfy-kitchen[cublas]"

# ---------------------------------------------------------------------------
# Layer 6: Build checks
#
# No node requirement may replace the pinned torch. ComfyUI then loads every
# custom node on the CPU and exits (--quick-test-for-ci); "IMPORT FAILED" or
# "PRESTARTUP FAILED" fails the build. Its run state goes to scratch paths and
# an in-memory database: the default database lives in $COMFYUI_DIR/user,
# which startup.sh treats as an old image's user data to migrate.
# ---------------------------------------------------------------------------
RUN python3 -c "import torch, torchvision, torchaudio; \
assert torch.__version__ == '2.11.0+cu128', torch.__version__; \
assert torchvision.__version__ == '0.26.0+cu128', torchvision.__version__; \
assert torchaudio.__version__ == '2.11.0+cu128', torchaudio.__version__" \
    && mkdir -p /tmp/ods-quick-test/user /tmp/ods-quick-test/output \
        /tmp/ods-quick-test/input /tmp/ods-quick-test/temp \
    && cd "$COMFYUI_DIR" \
    && { python3 main.py --cpu --quick-test-for-ci --disable-assets-autoscan \
            --database-url sqlite:///:memory: \
            --user-directory /tmp/ods-quick-test/user \
            --output-directory /tmp/ods-quick-test/output \
            --input-directory /tmp/ods-quick-test/input \
            --temp-directory /tmp/ods-quick-test/temp \
            > /tmp/ods-quick-test.log 2>&1 \
        || { cat /tmp/ods-quick-test.log; exit 1; }; } \
    && cat /tmp/ods-quick-test.log \
    && ! grep -E "IMPORT FAILED|PRESTARTUP FAILED" /tmp/ods-quick-test.log \
    && test ! -e "$COMFYUI_DIR/user" \
    && rm -rf /tmp/ods-quick-test /tmp/ods-quick-test.log

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
