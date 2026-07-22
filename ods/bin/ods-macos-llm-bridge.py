#!/usr/bin/env python3
"""Peer-filtered TCP bridge from Colima to a loopback-only macOS service."""

from __future__ import annotations

import argparse
import ipaddress
import logging
import signal
import socket
import socketserver
import threading
from collections.abc import Iterable
from typing import Union

logger = logging.getLogger("ods-macos-llm-bridge")
AllowedNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


def parse_allowed_networks(values: Iterable[str]) -> tuple[AllowedNetwork, ...]:
    """Parse explicit peer addresses or CIDRs into an immutable allowlist."""
    networks = []
    for value in values:
        networks.append(ipaddress.ip_network(value, strict=False))
    return tuple(networks)


def peer_is_allowed(
    address: str,
    allowed_networks: Iterable[AllowedNetwork] = (),
) -> bool:
    """Allow host loopback helpers plus explicitly configured VM peers."""
    try:
        peer = ipaddress.ip_address(address)
    except ValueError:
        return False
    return peer.is_loopback or any(peer in network for network in allowed_networks)


def _pump(source: socket.socket, destination: socket.socket) -> None:
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            destination.sendall(data)
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _enable_tcp_keepalive(connection: socket.socket) -> None:
    """Reclaim half-open bridge tunnels without timing out healthy idle calls."""
    try:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        idle_option = getattr(socket, "TCP_KEEPIDLE", None)
        if idle_option is None:
            idle_option = getattr(socket, "TCP_KEEPALIVE", None)
        if idle_option is not None:
            connection.setsockopt(socket.IPPROTO_TCP, idle_option, 60)
        if hasattr(socket, "TCP_KEEPINTVL"):
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        if hasattr(socket, "TCP_KEEPCNT"):
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    except OSError as exc:
        logger.debug("Could not configure TCP keepalive: %s", exc)


class LlmBridgeHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        peer = str(self.client_address[0])
        if not peer_is_allowed(peer, self.server.allowed_networks):  # type: ignore[attr-defined]
            logger.warning("Rejected bridge client outside the peer allowlist: %s", peer)
            return

        server = self.server
        try:
            upstream = socket.create_connection(
                (server.target_host, server.target_port),  # type: ignore[attr-defined]
                timeout=10,
            )
        except OSError as exc:
            logger.debug("Native llama-server is not ready: %s", exc)
            return

        with upstream:
            _enable_tcp_keepalive(self.request)
            _enable_tcp_keepalive(upstream)
            self.request.settimeout(None)
            upstream.settimeout(None)
            request_to_upstream = threading.Thread(
                target=_pump,
                args=(self.request, upstream),
                daemon=True,
            )
            upstream_to_request = threading.Thread(
                target=_pump,
                args=(upstream, self.request),
                daemon=True,
            )
            request_to_upstream.start()
            upstream_to_request.start()
            request_to_upstream.join()
            upstream_to_request.join()


class LlmBridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    # The Models page fans out status polls while starting an action. The
    # socketserver default backlog of 5 can drop the action connection.
    request_queue_size = 128
    # One handler plus two pump threads are used per live tunnel. macOS
    # launchd commonly budgets 32 threads for this LaunchAgent, so eight live
    # tunnels leave headroom for the main thread and shutdown handling.
    max_connections = 8
    connection_slot_timeout = 10.0

    def __init__(
        self,
        server_address: tuple[str, int],
        target_address: tuple[str, int],
        allowed_peers: Iterable[str] = (),
    ) -> None:
        self.target_host, self.target_port = target_address
        self.allowed_networks = parse_allowed_networks(allowed_peers)
        self._connection_slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__(server_address, LlmBridgeHandler)

    def process_request(self, request: socket.socket, client_address) -> None:
        if not self._connection_slots.acquire(timeout=self.connection_slot_timeout):
            logger.warning(
                "Bridge connection limit reached; rejecting %s after %.1fs",
                client_address[0],
                self.connection_slot_timeout,
            )
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connection_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()


def main() -> None:
    parser = argparse.ArgumentParser(description="ODS macOS Colima LLM bridge")
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=8080)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, default=18080)
    parser.add_argument(
        "--allow-peer",
        action="append",
        default=[],
        help="Allowed peer IP or CIDR; may be repeated (loopback is always allowed)",
    )
    args = parser.parse_args()

    if args.listen_host == args.target_host and args.listen_port == args.target_port:
        parser.error("listen and target addresses must differ")
    try:
        parse_allowed_networks(args.allow_peer)
    except ValueError as exc:
        parser.error(f"invalid --allow-peer value: {exc}")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    server = LlmBridgeServer(
        (args.listen_host, args.listen_port),
        (args.target_host, args.target_port),
        args.allow_peer,
    )

    def request_shutdown(signum, _frame) -> None:
        logger.info("Received signal %s; shutting down", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    logger.info(
        "Listening on %s:%d for loopback plus %s; forwarding to %s:%d",
        args.listen_host,
        args.listen_port,
        ", ".join(args.allow_peer) or "no explicit peers",
        args.target_host,
        args.target_port,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
