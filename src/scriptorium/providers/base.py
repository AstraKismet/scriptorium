"""Provider interface.

A provider takes a system prompt and a user message and returns text. That is the
entire contract — batching, JSON repair, placeholder validation, and retry of
individual segments all live above this layer in :mod:`scriptorium.translate`,
so adding a backend never means reimplementing the pipeline.
"""

import json
import os
import socket
import time
import urllib.error
import urllib.request


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
        return f"{self.name} ({self.kind}: {self.model} @ {self.spec.get('base_url','')})"

    def complete(self, system, user):  # pragma: no cover - interface
        raise NotImplementedError

    # -- transport ---------------------------------------------------------
    def _post(self, url, payload, headers):
        body = json.dumps(payload).encode("utf-8")
        last = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=body, method="POST")
            for k, v in {**headers, **self.extra_headers}.items():
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                last = ProviderError(f"{self.name}: HTTP {e.code} — {detail}")
                if e.code in (408, 409, 425, 429, 500, 502, 503, 504):
                    time.sleep(min(2 ** attempt, 20))
                    continue
                raise last from e
            except urllib.error.URLError as e:
                last = ProviderError(
                    f"{self.name}: cannot reach {url} — {e.reason}. "
                    "For a local server, check that it is running and that base_url "
                    "ends in /v1.")
                time.sleep(min(2 ** attempt, 20))
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
                time.sleep(min(2 ** attempt, 20))
        raise last or ProviderError(f"{self.name}: request failed")
