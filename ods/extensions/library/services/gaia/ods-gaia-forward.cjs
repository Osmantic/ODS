#!/usr/bin/env node
// Publish GAIA's loopback-only backend on every container interface.
//
// Usage: ods-gaia-forward <listen-port> <backend-port>
//
// AMD's full-mode backend (`gaia chat --ui`) hard-codes uvicorn's host to
// 127.0.0.1, so the dashboard's health check, the published host port and
// every other container on ods-network get "connection refused". This plain
// TCP forwarder listens on 0.0.0.0:<listen-port> and relays each connection
// to 127.0.0.1:<backend-port> unchanged.
//
// It also watches the backend: once the backend has accepted a connection,
// losing it for three consecutive probes exits non-zero. gaia-ui's Node
// wrapper can outlive its Python child, and the entrypoint turns this exit
// into a container exit so Docker's restart policy applies. Until the backend
// first comes up (the first start installs it) the forwarder only waits.
// ODS_GAIA_PROBE_INTERVAL_MS sets the probe interval (default 5000).

"use strict";

const net = require("net");

const LISTEN_HOST = "0.0.0.0";
const BACKEND_HOST = "127.0.0.1";
const PROBE_FAILURES_BEFORE_EXIT = 3;
const PROBE_TIMEOUT_MS = 2000;

function parsePort(value, name) {
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    console.error(`ods-gaia-forward: invalid ${name} port: ${value}`);
    process.exit(2);
  }
  return port;
}

const listenPort = parsePort(process.argv[2], "listen");
const backendPort = parsePort(process.argv[3], "backend");
const probeIntervalMs = Number(process.env.ODS_GAIA_PROBE_INTERVAL_MS || 5000);
if (!Number.isInteger(probeIntervalMs) || probeIntervalMs < 50) {
  console.error(`ods-gaia-forward: invalid ODS_GAIA_PROBE_INTERVAL_MS: ${process.env.ODS_GAIA_PROBE_INTERVAL_MS}`);
  process.exit(2);
}

// Ends of a relay that the peer resets or refuses; anything else is logged.
const EXPECTED_SOCKET_ERRORS = new Set(["ECONNREFUSED", "ECONNRESET", "EPIPE", "ETIMEDOUT"]);

function relay(client) {
  const backend = net.connect({ host: BACKEND_HOST, port: backendPort, allowHalfOpen: true });
  const closeBoth = (error) => {
    if (error && !EXPECTED_SOCKET_ERRORS.has(error.code)) {
      console.error(`ods-gaia-forward: connection error: ${error.message}`);
    }
    client.destroy();
    backend.destroy();
  };
  client.on("error", closeBoth);
  backend.on("error", closeBoth);
  client.on("close", () => backend.destroy());
  backend.on("close", () => client.destroy());
  client.pipe(backend);
  backend.pipe(client);
}

const server = net.createServer({ allowHalfOpen: true }, relay);
server.on("error", (error) => {
  console.error(`ods-gaia-forward: cannot listen on ${LISTEN_HOST}:${listenPort}: ${error.message}`);
  process.exit(1);
});
server.listen(listenPort, LISTEN_HOST, () => {
  console.log(`ods-gaia-forward: ${LISTEN_HOST}:${listenPort} -> ${BACKEND_HOST}:${backendPort}`);
});

function probeBackend() {
  return new Promise((resolve) => {
    const socket = net.connect({ host: BACKEND_HOST, port: backendPort });
    const done = (up) => {
      socket.destroy();
      resolve(up);
    };
    socket.setTimeout(PROBE_TIMEOUT_MS, () => done(false));
    socket.once("connect", () => done(true));
    socket.once("error", () => done(false));
  });
}

let backendSeen = false;
let failures = 0;

async function watchBackend() {
  const up = await probeBackend();
  if (up) {
    if (!backendSeen) {
      console.log(`ods-gaia-forward: GAIA backend is accepting connections on ${BACKEND_HOST}:${backendPort}`);
    }
    backendSeen = true;
    failures = 0;
  } else if (backendSeen) {
    failures += 1;
    if (failures >= PROBE_FAILURES_BEFORE_EXIT) {
      console.error(`ods-gaia-forward: GAIA backend stopped accepting connections on ${BACKEND_HOST}:${backendPort}; exiting`);
      process.exit(1);
    }
  }
  setTimeout(watchBackend, probeIntervalMs);
}

watchBackend();

for (const signal of ["SIGTERM", "SIGINT"]) {
  process.on(signal, () => process.exit(0));
}
