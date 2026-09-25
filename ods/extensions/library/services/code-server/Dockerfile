FROM ghcr.io/coder/code-server:4.138.0@sha256:81706b71ace316157850cbcfb574714d1067249c107a415b3a03dc6b613ebfbc
USER root
RUN mkdir -p /home/coder/Playground /home/coder/.config /home/coder/.local/share/code-server \
    && chown -R 1000:1000 /home/coder
USER 1000:1000
