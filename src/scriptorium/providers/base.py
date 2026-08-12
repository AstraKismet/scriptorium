"""Provider interface.

A provider takes a system prompt and a user message and returns text. That is the
entire contract — batching, JSON repair, placeholder validation, and retry of
individual segments all live above this layer in :mod:`scriptorium.translate`,
so adding a backend never means reimplementing the pipeline.
"""

import json
import os
import random
import socket
import time
import urllib.error
import urllib.request

from ..config import printable_url

# Transient by contract: a timeout, a conflict, "too early", a rate limit, and
# the 5xx family a gateway emits while a local runtime is still loading weights.
_RETRYABLE = (408, 409, 425, 429, 500, 502, 503, 504)
_MAX_BACKOFF = 20.0


class ProviderError(RuntimeError):
    pass


class Provider:
    kind = "base"

    def __init__(self, name, spec):
        self.name = name
        self.spec = spec
        self.model = spec.get("model", "")
        self.timeout = float(spec.get("timeout", 120))
        self.temperature = float(spec.get("temperature", 0.2))
        self.max_tokens = int(spec.get("max_tokens", 4096))
        self.retries = int(spec.get("retries", 3))
        self.extra_headers = spec.get("headers", {}) or {}

    # -- credentials -------------------------------------------------------
    @property
    def api_key(self):
        """Read from the environment only.

        Keys are never written to config or state — a project directory that
        gets committed or shared cannot leak one.
        """
        env = self.spec.get("api_key_env") or ""
        return os.environ.get(env, "") if env else ""

    def describe(self):
        # `printable_url`, not the raw value. This line is a display surface —
        # it is the first thing `lx translate` prints and the first entry of the
        # workbench's job log, which `POST /api/job` hands back verbatim — and
        # invariant 6 says every display surface shares one answer about what is
        # printable over a `base_url`. It did not: `lx config get` and
        # `lx providers` masked a hand-edited `https://user:SECRET@host/v1`
        # while this printed it in full, into a log and into an HTTP response.
        # Found by the security-tier re-derivation of the frozen workbench
        # contract, 2026-08-13.
        return f"{self.name} ({self.kind}: {self.model} @ {printable_url(self.spec.get('base_url', ''))})"

    def complete(self, system, user):  # pragma: no cover - interface
        raise NotImplementedError

    # -- transport ---------------------------------------------------------
    def _backoff(self, attempt, retry_after=None):
        """How long to wait before the next attempt.

        `Retry-After` wins when the server sends a number, because a hosted API
        returning 429 knows its own window and an exponential guess does not.
        Its HTTP-date spelling is deliberately not honoured: parsing a date to
        wait on it is more machinery than this case earns, and falling through
        to our own backoff is never wrong, only slower.

        Jitter because a batch runs several requests concurrently against one
        server. Without it every one of them fails together and then retries in
        the same instant, which is the burst that caused the failure arriving
        again on schedule.
        """
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), _MAX_BACKOFF)
            except ValueError:
                pass  # an HTTP-date; use the backoff we control
        return min(2 ** attempt + random.uniform(0, 1), _MAX_BACKOFF)

    def _post(self, url, payload, headers):
        body = json.dumps(payload).encode("utf-8")
        last = None
        for attempt in range(self.retries + 1):
            # Every sleep below is guarded by this. The loop used to wait after
            # its final attempt and then leave and raise anyway, so `retries=0`
            # cost a second of pure latency per failed call — which the suite
            # paid on every run.
            final = attempt == self.retries
            req = urllib.request.Request(url, data=body, method="POST")
            for k, v in {**headers, **self.extra_headers}.items():
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                last = ProviderError(f"{self.name}: HTTP {e.code} — {detail}")
                if e.code not in _RETRYABLE:
                    raise last from e
                if not final:
                    time.sleep(self._backoff(attempt, e.headers.get("Retry-After")))
            except urllib.error.URLError as e:
                last = ProviderError(
                    # Masked for the same reason `describe` is: this message
                    # reaches `/api/job`'s `error` field, and a URL is the one
                    # place a credential hides in something nobody thinks of as
                    # a credential. `printable_url` keeps scheme, host and path,
                    # so the "ends in /v1" advice still reads.
                    f"{self.name}: cannot reach {printable_url(url)} — {e.reason}. "
                    "For a local server, check that it is running and that base_url "
                    "ends in /v1.")
                if not final:
                    time.sleep(self._backoff(attempt))
            # `socket.timeout` only became an alias of the builtin in 3.10, and
            # 3.9 is the declared floor and a CI matrix entry. There a stalled
            # read — headers received, body never arriving — raises the socket
            # class, which is not a `TimeoutError` and not a `URLError` either,
            # so it escaped both handlers and reached the user as a bare OSError
            # instead of the message below. On 3.10+ the two names are one class
            # and the tuple is a duplicate, which costs nothing.
            except (TimeoutError, socket.timeout):
                last = ProviderError(
                    f"{self.name}: timed out after {self.timeout}s. Local models on "
                    "CPU are slow — raise `timeout` or lower `batch.size`.")
                if not final:
                    time.sleep(self._backoff(attempt))
        raise last or ProviderError(f"{self.name}: request failed")
