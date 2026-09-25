FROM ghcr.io/dgtlmoon/changedetection.io:0.60.7@sha256:096dae27b5d677b89f0e810fff95a70403271aa3ff3b6437952d2db9be7e74c5
USER root
RUN mkdir -p /datastore && chown -R 1000:1000 /datastore
# Fresh ODS installs must not start monitoring upstream example sites.
# Fail the build if the pinned source no longer has the expected startup default.
RUN python -c "from pathlib import Path; p=Path('/app/changedetectionio/__init__.py'); s=p.read_text(); old='include_default_watches = True'; assert s.count(old)==1; p.write_text(s.replace(old, 'include_default_watches = False'))"
COPY ods-start.py /app/ods-start.py
USER 1000:1000
