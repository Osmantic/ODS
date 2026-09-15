FROM ghcr.io/marimo-team/marimo:0.24.0@sha256:27e70f3dce76eb79bbf1a7731f2f18c3a85271747919aa5589f4ab8aa6377679
USER root
# The published container currently trails the Python release. Include the
# current cookie-token fix instead of shipping the older server from the base.
RUN uv pip install --no-cache-dir 'marimo==0.24.2' && uv pip check
COPY --chown=appuser:appuser entrypoint.py /opt/ods-marimo-entrypoint.py
USER appuser
WORKDIR /workspace
ENTRYPOINT ["python", "/opt/ods-marimo-entrypoint.py"]
