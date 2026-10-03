FROM scratch

ARG TREE_SHA256
ARG EXECUTABLE_SHA256

COPY --chown=1000:1000 --chmod=0555 server /opt/fixture/bin/server

LABEL org.osmantic.pixel.capability-tree="${TREE_SHA256}" \
      org.osmantic.pixel.capability-executable="${EXECUTABLE_SHA256}" \
      org.osmantic.pixel.mcp-version="2026-07-28"

USER 1000:1000
ENTRYPOINT ["/opt/fixture/bin/server"]
