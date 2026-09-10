"""Keep optional-service DNS probes out of the foreground event-loop pool."""

import asyncio
import socket
from concurrent.futures import ThreadPoolExecutor

from aiohttp.abc import AbstractResolver


class ServiceHealthResolver(AbstractResolver):
    """Bound native lookups independently of chat/host-agent DNS resolution."""

    def __init__(self, workers: int = 4):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ods-health-dns")
        self._slots = asyncio.Semaphore(workers)
        self._closed = False

    @staticmethod
    def _lookup(host, port, family):
        records = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
        results = []
        for resolved_family, _, protocol, _, address in records:
            if resolved_family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            resolved_host, resolved_port = address[:2]
            if resolved_family == socket.AF_INET6 and len(address) > 3 and address[3]:
                resolved_host, numeric_port = socket.getnameinfo(
                    address, socket.NI_NUMERICHOST | socket.NI_NUMERICSERV
                )
                resolved_port = int(numeric_port)
            results.append({
                "hostname": host, "host": resolved_host, "port": resolved_port,
                "family": resolved_family, "proto": protocol,
                "flags": socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
            })
        return results

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if self._closed:
            raise RuntimeError("Service health resolver is closed")
        await self._slots.acquire()
        if self._closed:
            self._slots.release()
            raise RuntimeError("Service health resolver is closed")
        loop = asyncio.get_running_loop()
        try:
            pending = self._pool.submit(self._lookup, host, port, family)
        except BaseException:
            self._slots.release()
            raise

        def finished(_future):
            # Cancelling an HTTP request cannot interrupt libc getaddrinfo.
            # Keep its slot reserved until the native lookup really terminates.
            try:
                loop.call_soon_threadsafe(self._slots.release)
            except RuntimeError:
                pass  # The owning loop has already shut down.

        pending.add_done_callback(finished)
        return await asyncio.wrap_future(pending)

    async def close(self):
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)
