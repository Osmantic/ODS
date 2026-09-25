FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818 AS omp-native-extractor

ADD --checksum=sha256:6c75331bf09d5a9e9433bd592b3ee993d751a15d5b7450c1a334cc0684996f30 https://github.com/can1357/oh-my-pi/releases/download/v17.2.12/omp-linux-x64 /tmp/omp
RUN --network=none chmod 0555 /tmp/omp \
    && install -d -m 0700 /tmp/omp-home /tmp/omp-home/agent \
    && printf '%s\n' 'Pixel pinned native extraction probe.' > /tmp/omp-probe \
    && HOME=/tmp/omp-home PI_CODING_AGENT_DIR=/tmp/omp-home/agent /tmp/omp read /tmp/omp-probe >/dev/null \
    && test "$(stat -c %s /tmp/omp-home/.omp/natives/17.2.12/pi_natives.linux-x64-modern.node)" = "152979232" \
    && test "$(stat -c %s /tmp/omp-home/.omp/natives/17.2.12/pi_natives.linux-x64-baseline.node)" = "153034792" \
    && echo 'a168d084b6d212eea8582c17f13931b123325da4da3b6daa767f34ee82b7ec27  /tmp/omp-home/.omp/natives/17.2.12/pi_natives.linux-x64-modern.node' | sha256sum -c - \
    && echo 'ae64262a74785035dfb0d03e09d0374648e2bdb2dec79f5efd1db14247dfbd91  /tmp/omp-home/.omp/natives/17.2.12/pi_natives.linux-x64-baseline.node' | sha256sum -c - \
    && install -d -m 0555 /omp-native \
    && install -m 0444 /tmp/omp-home/.omp/natives/17.2.12/pi_natives.linux-x64-modern.node /omp-native/pi_natives.linux-x64-modern.node \
    && install -m 0444 /tmp/omp-home/.omp/natives/17.2.12/pi_natives.linux-x64-baseline.node /omp-native/pi_natives.linux-x64-baseline.node

FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates xz-utils gdb=13.1-3 python3=3.11.2-1+b1 python-is-python3=3.11.2-1+deb12u1 python3-debugpy=1.6.6+ds-1 sqlite3=3.40.1-2+deb12u2 \
    && test "$(gdb --version | head -n 1)" = "GNU gdb (Debian 13.1-3) 13.1" \
    && test "$(python --version 2>&1)" = "Python 3.11.2" \
    && python -c 'import debugpy, importlib.util; assert debugpy.__version__ == "1.6.3+git20221103.a2a3328"; assert importlib.util.find_spec("debugpy.adapter") is not None' \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /var/cache/ldconfig/aux-cache /var/log/alternatives.log /var/log/apt/history.log /var/log/apt/term.log /var/log/dpkg.log

ADD --checksum=sha256:b9b6f86ed85d4ef5e0211eaebf75d057bd8bb520bba438a95dd0f4e42234bbfe https://files.pythonhosted.org/packages/b5/55/f9641a4eebcc2f4df631287d6c3b9ed2eea3b92644f93acbad825e3972b6/duckdb-1.5.5-cp311-cp311-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl /tmp/duckdb.whl
ADD --checksum=sha256:22aa0cb92a1ee2d60d6a15a638b2e8e0dd99aea21ac0cd8fb29da8e382e075a9 https://files.pythonhosted.org/packages/2b/fe/0888040a24e4504098b85d8ad486b14cb01cf6b030bbe479dfc2dcffc2ac/polars-1.43.2-py3-none-any.whl /tmp/polars.whl
ADD --checksum=sha256:6d5a7ae004a2723ebf4427f6d6a639f30f86af4cf077075f6b35d04711154fc3 https://files.pythonhosted.org/packages/62/60/64deacb3abc70c52e2d88a808a052d1621c86a48fe9194f2c065579ab1cd/polars_runtime_32-1.43.2-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl /tmp/polars-runtime.whl
RUN --network=none install -d -m 0555 /opt/pixel/data-runtime \
    && python3 -m zipfile -e /tmp/duckdb.whl /opt/pixel/data-runtime \
    && python3 -m zipfile -e /tmp/polars.whl /opt/pixel/data-runtime \
    && python3 -m zipfile -e /tmp/polars-runtime.whl /opt/pixel/data-runtime \
    && find /opt/pixel/data-runtime -type d -exec chmod 0555 {} + \
    && find /opt/pixel/data-runtime -type f -exec chmod 0444 {} + \
    && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/pixel/data-runtime python3 -B -c 'import duckdb, polars, sqlite3, sys; assert sys.version_info[:3] == (3, 11, 2); assert duckdb.__version__ == "1.5.5"; assert polars.__version__ == "1.43.2"; assert sqlite3.sqlite_version == "3.40.1"' \
    && test "$(sqlite3 --version | cut -d' ' -f1)" = "3.40.1" \
    && find /opt/pixel/data-runtime -type d -name __pycache__ -prune -exec rm -rf {} + \
    && rm -f /tmp/duckdb.whl /tmp/polars.whl /tmp/polars-runtime.whl

ADD --checksum=sha256:bd5c488fc20fa237a944279bf32cae2f986cf10d5d5d9e8705819859daeb2f4a https://registry.npmjs.org/pyright/-/pyright-1.1.411.tgz /tmp/pyright-runtime.tgz
ADD --checksum=sha256:9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578 https://nodejs.org/dist/v22.23.1/node-v22.23.1-linux-x64.tar.xz /tmp/node-runtime.tar.xz
RUN install -d -m 0755 /opt/node /opt/pixel/deploy/agent-comparison /opt/pixel/deploy/work-controller /opt/pixel/deploy/work-model-proxy /opt/pixel/deploy/work-runner /opt/pixel/scripts/lib /opt/pixel/schemas /inputs /candidate \
    && tar -xJf /tmp/node-runtime.tar.xz --strip-components=1 -C /opt/node \
    && test "$(/opt/node/bin/node --version)" = "v22.23.1" \
    && install -d -m 0555 /opt/pixel/builder-runtime/pyright \
    && tar -xzf /tmp/pyright-runtime.tgz --strip-components=1 -C /opt/pixel/builder-runtime/pyright \
    && find /opt/pixel/builder-runtime/pyright -type d -exec chmod 0555 {} + \
    && find /opt/pixel/builder-runtime/pyright -type f -exec chmod 0444 {} + \
    && chmod 0555 /opt/pixel/builder-runtime/pyright/index.js /opt/pixel/builder-runtime/pyright/langserver.index.js \
    && ln -s /opt/pixel/builder-runtime/pyright/index.js /usr/local/bin/pyright \
    && ln -s /opt/pixel/builder-runtime/pyright/langserver.index.js /usr/local/bin/pyright-langserver \
    && test "$(PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin pyright --version)" = "pyright 1.1.411" \
    && rm -f /tmp/node-runtime.tar.xz /tmp/pyright-runtime.tgz \
    && groupadd --gid 1000 pixel-work \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin pixel-work \
    && install -d -o 1000 -g 1000 -m 0700 /tmp/pixel-work /run/pixel/capability /run/pixel/capability/requests /run/pixel/capability/responses

COPY --chmod=0444 deploy/work-model-proxy/proxy.mjs /opt/pixel/deploy/work-model-proxy/proxy.mjs
COPY --chmod=0444 deploy/work-model-proxy/inference-policy.mjs /opt/pixel/deploy/work-model-proxy/inference-policy.mjs
COPY --chmod=0444 deploy/agent-comparison/inference-boundary.mjs /opt/pixel/deploy/agent-comparison/inference-boundary.mjs
COPY --chmod=0444 deploy/agent-comparison/research-mcp-server.mjs /opt/pixel/deploy/agent-comparison/research-mcp-server.mjs
COPY --chmod=0444 deploy/work-controller/model-qualification.mjs /opt/pixel/deploy/work-controller/model-qualification.mjs
COPY --chmod=0444 deploy/work-controller/model-qualification-runner.mjs /opt/pixel/deploy/work-controller/model-qualification-runner.mjs
COPY --chmod=0444 deploy/work-controller/model-qualification-bridge.mjs /opt/pixel/deploy/work-controller/model-qualification-bridge.mjs
COPY --chmod=0444 deploy/work-controller/model-qualification-container.mjs /opt/pixel/deploy/work-controller/model-qualification-container.mjs
COPY --chmod=0444 deploy/work-runner/builder-volume.mjs /opt/pixel/deploy/work-runner/builder-volume.mjs
COPY --chmod=0444 deploy/work-runner/verifier-command.mjs /opt/pixel/deploy/work-runner/verifier-command.mjs
COPY --chmod=0444 deploy/work-runner/research-tool.mjs /opt/pixel/deploy/work-runner/research-tool.mjs
COPY --chmod=0444 deploy/work-runner/capability-tool.mjs /opt/pixel/deploy/work-runner/capability-tool.mjs
COPY --chmod=0444 deploy/work-runner/data-runtime.json /opt/pixel/deploy/work-runner/data-runtime.json
COPY --chmod=0444 deploy/work-runner/builder-runtime.json /opt/pixel/deploy/work-runner/builder-runtime.json
COPY --chmod=0444 deploy/work-runner/omp-runtime.json /opt/pixel/deploy/work-runner/omp-runtime.json
COPY --chmod=0444 deploy/work-runner/builder-agent.md /opt/pixel/deploy/work-runner/builder-agent.md
COPY --chmod=0555 deploy/work-runner/builder-entrypoint.sh /opt/pixel/deploy/work-runner/builder-entrypoint.sh
COPY --chmod=0444 deploy/work-runner/data-artifacts.mjs /opt/pixel/deploy/work-runner/data-artifacts.mjs
COPY --chmod=0555 deploy/work-runner/private-entrypoint.sh /opt/pixel/deploy/work-runner/private-entrypoint.sh
COPY --chmod=0444 scripts/lib/secure-files.mjs /opt/pixel/scripts/lib/secure-files.mjs
COPY --chmod=0444 scripts/lib/json-schema.mjs /opt/pixel/scripts/lib/json-schema.mjs
COPY --chmod=0444 scripts/lib/work-contract.mjs /opt/pixel/scripts/lib/work-contract.mjs
COPY --chmod=0444 schemas/*.json /opt/pixel/schemas/
RUN find /opt/pixel/schemas -type d -exec chmod 0555 {} + \
    && find /opt/pixel/schemas -type f -exec chmod 0444 {} +
COPY --from=omp-native-extractor --chmod=0444 /omp-native/pi_natives.linux-x64-modern.node /opt/pi_natives.linux-x64-modern.node
COPY --from=omp-native-extractor --chmod=0444 /omp-native/pi_natives.linux-x64-baseline.node /opt/pi_natives.linux-x64-baseline.node

LABEL org.opencontainers.image.title="Pixel Deep Work runner" \
      org.opencontainers.image.version="4.3.27" \
      org.opencontainers.image.base.name="debian:bookworm-slim" \
      org.osmantic.pixel.node-runtime="22.23.1" \
      org.osmantic.pixel.omp-version="17.2.12" \
      org.osmantic.pixel.omp-runtime="pixel-omp-linux-x64-runtime-v1" \
      org.osmantic.pixel.omp-minimum-worker-memory-mib="1536" \
      org.osmantic.pixel.data-runtime="pixel-local-data-runtime-v1" \
      org.osmantic.pixel.builder-runtime="pixel-builder-code-intelligence-runtime-v1" \
      org.osmantic.pixel.builder-minimum-memory-mib="2048" \
      org.osmantic.pixel.gdb-version="13.1" \
      org.osmantic.pixel.python-version="3.11.2" \
      org.osmantic.pixel.pyright-version="1.1.411" \
      org.osmantic.pixel.debugpy-version="1.6.3+git20221103.a2a3328" \
      org.osmantic.pixel.sqlite-version="3.40.1" \
      org.osmantic.pixel.duckdb-version="1.5.5" \
      org.osmantic.pixel.polars-version="1.43.2" \
      org.osmantic.pixel.default-uid="1000"

ENV PYTHONPATH=/opt/pixel/data-runtime
USER 1000:1000
WORKDIR /tmp/pixel-work
ENTRYPOINT ["/opt/node/bin/node", "/opt/pixel/deploy/work-model-proxy/proxy.mjs"]
