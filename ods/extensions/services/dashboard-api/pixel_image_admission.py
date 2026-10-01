"""No-queue image work admission, retained until canceled workers really exit."""
import asyncio
import threading


class ImageWorkBudget:
    def __init__(self, capacity=1):
        self._slots = threading.BoundedSemaphore(capacity)

    def acquire(self):
        return ImageWorkLease(self._slots) if self._slots.acquire(blocking=False) else None


class ImageWorkLease:
    def __init__(self, slots):
        self._slots = slots
        self._lock = threading.Lock()
        self._references = 1

    def release(self):
        with self._lock:
            if self._references <= 0:
                raise RuntimeError("image work lease already released")
            self._references -= 1
            if self._references == 0:
                self._slots.release()

    async def run(self, function, *args):
        with self._lock:
            if self._references <= 0:
                raise RuntimeError("image work lease already released")
            self._references += 1

        def execute():
            try:
                return function(*args)
            finally:
                self.release()

        try:
            future = asyncio.get_running_loop().run_in_executor(None, execute)
        except BaseException:
            self.release()
            raise
        # A canceled HTTP request must not cancel this future or free its slot
        # while Pillow/base64/SQLite still holds memory on the worker thread.
        future.add_done_callback(lambda item: None if item.cancelled() else item.exception())
        return await asyncio.shield(future)
