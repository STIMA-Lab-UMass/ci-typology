import threading
import time


class RateLimiter:
    """
    Simple thread-safe rate limiter that enforces a minimum interval
    between successive acquisitions. Use from_per_minute() helper to
    convert a requests-per-minute budget.
    """

    def __init__(self, min_interval_seconds: float = 0.0):
        self.min_interval = max(0.0, float(min_interval_seconds or 0.0))
        self._lock = threading.Lock()
        self._next_time = 0.0

    @classmethod
    def from_per_minute(cls, requests_per_minute: float):
        if not requests_per_minute:
            return cls(0.0)
        interval = 60.0 / float(requests_per_minute)
        return cls(interval)

    def wait(self):
        if self.min_interval <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                if now >= self._next_time:
                    self._next_time = now + self.min_interval
                    return
                wait_time = self._next_time - now
            time.sleep(wait_time)

