FROM nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7
# Preserve upstream entrypoint, CMD, user, plugin discovery and LICENSE.
COPY manual-resume.patch source-hashes.json apply.py /opt/ods-hermes-manual-resume/
RUN /opt/hermes/.venv/bin/python /opt/ods-hermes-manual-resume/apply.py
LABEL com.osmantic.hermes.resume-contract="manual-v1" \
      com.osmantic.hermes.upstream-revision="f97608f178d1ffeca59860195ab7da295f7c8e5f"
