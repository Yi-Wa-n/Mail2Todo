"""Stop the web service after all browser streams and work become idle."""
from contextlib import contextmanager
import threading
import time

IDLE_SECONDS = 120


class IdleLifecycle:
    def __init__(self, timeout=IDLE_SECONDS, clock=time.monotonic):
        self.timeout = timeout
        self.clock = clock
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._active = 0
        self._last_activity = clock()
        self._closing = False

    @contextmanager
    def request(self):
        with self._lock:
            if self._closing:
                raise RuntimeError('控制台正在退出，请重新打开入口。')
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
                self._last_activity = self.clock()

    def should_stop(self, busy=False):
        with self._lock:
            if self._closing:
                return False
            if self._active or busy:
                self._last_activity = self.clock()
                return False
            if self.clock() - self._last_activity >= self.timeout:
                self._closing = True
                return True
            return False

    def watch(self, server, busy):
        while not self.stop_event.wait(1):
            if self.should_stop(busy=busy()):
                self.stop_event.set()
                server.shutdown()
                return
