"""Optional API key and per-client token-bucket rate limit (spec 13.1). Stdlib only."""

from __future__ import annotations

import hmac
import os
import time


class ApiKeyGuard:
    """If the env var named by ``[serving].api_key_env`` is set, ``/v1`` calls need a matching
    ``X-API-Key``."""

    def __init__(self, env_name: str):
        self.env_name = env_name

    @property
    def key(self) -> str | None:
        v = os.environ.get(self.env_name, "")
        return v or None

    @property
    def required(self) -> bool:
        return self.key is not None

    def check(self, presented: str | None) -> bool:
        key = self.key
        if key is None:
            return True
        if not presented:
            return False
        return hmac.compare_digest(presented.encode("utf-8"), key.encode("utf-8"))


class RateLimiter:
    """Token bucket per client IP; ``rps <= 0`` disables it."""

    def __init__(self, rps: float, burst: float | None = None, max_clients: int = 10000):
        self.rps = float(rps)
        self.burst = float(burst if burst is not None else max(1.0, 2 * self.rps))
        self.buckets: dict[str, list[float]] = {}
        self.max_clients = max_clients

    def allow(self, client: str, now: float | None = None) -> bool:
        if self.rps <= 0:
            return True
        now = time.monotonic() if now is None else now
        b = self.buckets.get(client)
        if b is None:
            if len(self.buckets) >= self.max_clients:
                self.buckets.clear()
            b = [self.burst, now]
            self.buckets[client] = b
        tokens = min(self.burst, b[0] + (now - b[1]) * self.rps)
        b[1] = now
        if tokens < 1.0:
            b[0] = tokens
            return False
        b[0] = tokens - 1.0
        return True
