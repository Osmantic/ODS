# Pixel Edge — internal OpenAI-compatible proxy from Open WebUI to host Pixel ingress
#
# Security: non-root, read-only rootfs, cap_drop ALL (enforced in compose),
# no host ports exposed. The Unix socket directory is mounted read-only.

FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e

LABEL org.opencontainers.image.source="https://github.com/Osmantic/ODS"
LABEL org.opencontainers.image.description="ODS Pixel Edge proxy"

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY pixel_edge.py ./
COPY chat_context.py ./
COPY image_message.py ./
COPY image_admission.py ./
COPY runtime_identity.py ./
COPY access_mode.py ./
COPY transition_gate.py ./
COPY edge_entrypoint.py ./
RUN chmod -R a+rX /app

# Non-root user
RUN useradd -m -u 1000 pixel
RUN install -d -m 0700 -o pixel -g pixel /pixel-transition-state

USER pixel

ENV PIXEL_EDGE_PORT_INTERNAL=9595
ENV PIXEL_TRANSITION_STATE_DIR=/pixel-transition-state

EXPOSE 9595

CMD ["python3", "edge_entrypoint.py"]
