# Pixel Edge — internal OpenAI-compatible proxy from Open WebUI to host Pixel ingress
#
# Security: non-root, read-only rootfs, cap_drop ALL (enforced in compose),
# no host ports exposed. The Unix socket directory is mounted read-only.

FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/Osmantic/ODS"
LABEL org.opencontainers.image.description="ODS Pixel Edge proxy"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pixel_edge.py ./
COPY transition_gate.py ./
COPY edge_entrypoint.py ./

# Non-root user
RUN useradd -m -u 1000 pixel
RUN install -d -m 0700 -o pixel -g pixel /pixel-transition-state

USER pixel

ENV PIXEL_EDGE_PORT_INTERNAL=9595
ENV PIXEL_TRANSITION_STATE_DIR=/pixel-transition-state

EXPOSE 9595

CMD ["python3", "edge_entrypoint.py"]
