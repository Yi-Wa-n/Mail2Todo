"""Stop the web service after all browser streams and work become idle."""
from contextlib import contextmanager
import threading
import time
import uuid

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
        self._writes = 0
        self.action = None
        self.instance = str(uuid.uuid4())

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

    @contextmanager
    def mutation(self):
        with self._lock:
            if self._closing:
                raise ValueError('服务正在退出或重启，请稍后重新打开')
            self._writes += 1
        try:
            yield
        finally:
            with self._lock:
                self._writes -= 1

    def begin_control(self, action, busy=False):
        if action not in ('exit', 'restart'):
            raise ValueError('无效服务操作')
        with self._lock:
            if self._closing:
                raise ValueError('服务正在退出或重启，请稍后重新打开')
            if self._writes or busy:
                raise ValueError('正在扫描或保存，请完成后再退出或重启')
            self._closing = True
            self.action = action

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
