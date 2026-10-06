FROM python:3.11-slim-trixie@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    USE_TF=0 USE_TORCH=1 TOKENIZERS_PARALLELISM=false \
    TORCH_DISABLE_NATIVE_JIT=1 HOME=/cache
COPY requirements.lock requirements-torch.lock /opt/ods-laya/
RUN python -m pip install --only-binary=:all: --no-deps --require-hashes -r /opt/ods-laya/requirements.lock \
    && python -m pip install --only-binary=:all: --no-deps --require-hashes -r /opt/ods-laya/requirements-torch.lock \
    && python -m pip check \
    && python -c "import torch; assert torch.__version__ == '2.14.0+cpu'; assert torch.ones(2).sum().item() == 2"
COPY server.py health.py /opt/ods-laya/
RUN mkdir /cache && chmod 1777 /cache
LABEL org.opencontainers.image.source="https://github.com/NandhaKishorM/laya" \
      org.opencontainers.image.version="0.3.28" \
      org.opencontainers.image.revision="a4a8921afebfd852bba0000475cfb6ab737a124c"
USER 1000:1000
EXPOSE 8000
CMD ["python", "/opt/ods-laya/server.py"]
