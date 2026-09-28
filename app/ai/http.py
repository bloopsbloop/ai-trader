"""Public GET transport with bounded retries and non-blocking rate-limit cooldown."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time
import math

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class RateLimited(requests.RequestException):
    pass


class PublicSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.headers["User-Agent"] = "AI-Trader-Paper-Research/1.0"
        retry = Retry(total=2, connect=2, read=2, status=2, backoff_factor=0.5,
                      allowed_methods=frozenset({"GET"}),
                      status_forcelist=(500, 502, 503, 504), respect_retry_after_header=False)
        self.mount("https://", HTTPAdapter(max_retries=retry))
        self.cooldown_until = 0.0

    def get(self, url, **kwargs):
        if time.monotonic() < self.cooldown_until:
            raise RateLimited("Public source is cooling down")
        response = super().get(url, **kwargs)
        if response.status_code in {418, 429}:
            raw = response.headers.get("Retry-After", "60")
            try:
                delay = float(raw)
                if not math.isfinite(delay):
                    raise ValueError("Invalid Retry-After")
                delay = max(1, delay)
            except ValueError:
                try:
                    delay = max(1, (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    delay = 60
            self.cooldown_until = time.monotonic() + delay
            response.close()
            raise RateLimited("Public source rate limit; retry on a later cycle")
        return response
