# =============================================================================
# llama-server — Intel Arc SYCL Build
#
# Builds llama.cpp from source using Intel oneAPI Base Toolkit.
# SYCL backend requires Intel Arc GPU with Level Zero runtime on host.
#
# Host prerequisites (Ubuntu/Debian):
#   apt install intel-opencl-icd intel-level-zero-gpu level-zero
#   usermod -aG video,render $USER   # re-login after
#   # Verify: clinfo | grep -i "intel arc"
#
# Build args:
#   ONEAPI_VERSION  oneAPI Base Toolkit image tag (default: 2025.0.0-0-devel-ubuntu22.04)
#   LLAMA_TAG       llama.cpp git tag to build (default: b8248)
#
# Layers ordered for optimal Docker cache re-use:
#   1. System build deps       — rarely changes; rebuilds only on ONEAPI_VERSION bump
#   2. Clone llama.cpp         — invalidated only when LLAMA_TAG changes
#   3. CMake configure + build — most expensive; fully cached between identical builds
#   4. Runtime stage           — only invalidated when the binary or oneAPI image changes
# =============================================================================

ARG ONEAPI_VERSION=2025.0.0-0-devel-ubuntu22.04

# ---------------------------------------------------------------------------
# Stage 1 — Build
# Intel oneAPI Base Toolkit provides: icx/icpx (SYCL compilers), oneMKL,
# Level Zero headers, and OpenCL headers needed to compile llama.cpp SYCL.
# ---------------------------------------------------------------------------
FROM intel/oneapi-basekit:${ONEAPI_VERSION} AS builder

ARG LLAMA_TAG=b8248
ENV DEBIAN_FRONTEND=noninteractive

# cmake + ninja for fast parallel builds; libcurl for model URL loading
RUN apt-get update && apt-get install -y --no-install-recommends \
        cmake ninja-build git \
        libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Clone at exact tag for reproducible builds
RUN git clone --branch "${LLAMA_TAG}" --depth 1 \
        https://github.com/ggml-org/llama.cpp /llama.cpp

WORKDIR /llama.cpp

# Configure:
#   GGML_SYCL       — enable SYCL backend
#   icx / icpx      — Intel LLVM-based C/C++ compiler with SYCL extensions
#   GGML_SYCL_F16   — FP16 compute path (Arc uses FP16 natively; improves throughput)
#   LLAMA_CURL      — enable curl-based model download inside container
RUN cmake -B build \
        -G Ninja \
        -DGGML_SYCL=ON \
        -DCMAKE_C_COMPILER=icx \
        -DCMAKE_CXX_COMPILER=icpx \
        -DGGML_SYCL_F16=ON \
        -DCMAKE_BUILD_TYPE=Release \
        -DLLAMA_CURL=ON \
    && cmake --build build --target llama-server -j$(nproc)

# ---------------------------------------------------------------------------
# Stage 2 — Runtime
#
# Reuse the same oneAPI Base Toolkit image so all SYCL runtime libraries
# (libsycl, Level Zero loader, oneMKL, OpenCL ICD) are present without
# manual extraction.  A slimmer image is achievable by copying only the
# SYCL runtime .so files, but that requires careful auditing of dependencies
# and is left as a future optimisation.
# ---------------------------------------------------------------------------
FROM intel/oneapi-basekit:${ONEAPI_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /llama.cpp/build/bin/llama-server /usr/local/bin/llama-server

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
    CMD curl -sf http://localhost:8080/health || exit 1

ENTRYPOINT ["llama-server"]
