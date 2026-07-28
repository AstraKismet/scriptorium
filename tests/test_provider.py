"""Provider tests against a mock OpenAI-compatible server.

This is the contract that matters for local deployment: the request must be
plain enough that llama.cpp, Ollama, LM Studio, and vLLM all accept it.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium.providers import build  # noqa: E402
from scriptorium.providers.base import ProviderError  # noqa: E402

SEEN = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n))
        SEEN["payload"] = payload
        SEEN["auth"] = self.headers.get("Authorization")
        body = json.dumps({"choices": [{"message": {"content": '{"s1": "ok"}'}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StallHandler(BaseHTTPRequestHandler):
    """Answers with headers and then goes quiet.

    A *read* timeout, which is a different exception from a connect timeout: the
    connect case never reaches a socket read and surfaces as `URLError`, which
    the provider always handled. This one stalls after the headers are on the
    wire, and that is the case Python 3.9 raises `socket.timeout` for.
    """

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "1024")   # promised, never sent
        self.end_headers()
        time.sleep(1.5)                              # longer than any client timeout here


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


class BusyHandler(BaseHTTPRequestHandler):
    """503 to everything, instantly.

    A retryable status from a server that answers at once is what isolates the
    sleep: connecting to a dead port does not, because a refused connection is
    instant on Linux but a one-second timeout on Windows, so the wait being
    measured would be the platform's, not ours.
    """

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = b'{"error": "loading model"}'
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def busy():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), BusyHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


RATE_LIMITED = {"calls": 0}


class RetryAfterHandler(BaseHTTPRequestHandler):
    """429 with `Retry-After: 0` once, then success.

    The `0` is the whole assertion. The exponential backoff for the first retry
    is a second or more, so the elapsed time of a call that succeeds on the
    second attempt is what distinguishes "read the header" from "ignored it".
    """

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        RATE_LIMITED["calls"] += 1
        if RATE_LIMITED["calls"] == 1:
            body = b'{"error": "slow down"}'
            self.send_response(429)
            self.send_header("Retry-After", "0")
        else:
            body = json.dumps({"choices": [{"message": {"content": '{"s1": "ok"}'}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def rate_limited():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), RetryAfterHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


@pytest.fixture(scope="module")
def stalling():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), StallHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def _cfg(url, **extra):
    return {"providers": {"local": {"kind": "openai", "base_url": url,
                                    "model": "test-model", "api_key_env": "", **extra}}}


def test_local_server_needs_no_auth_header(server):
    out = build("local", _cfg(server)).complete("sys", "user")
    assert out == '{"s1": "ok"}'
    assert SEEN["auth"] is None


def test_request_stays_minimal_by_default(server):
    build("local", _cfg(server)).complete("sys", "user")
    payload = SEEN["payload"]
    assert payload["stream"] is False
    assert "response_format" not in payload          # local runtimes often reject it
    assert "tools" not in payload
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]


def test_json_mode_is_opt_in(server):
    build("local", _cfg(server, json_mode=True)).complete("sys", "user")
    assert SEEN["payload"]["response_format"] == {"type": "json_object"}


def test_api_key_read_from_environment(server, monkeypatch):
    monkeypatch.setenv("LX_TEST_KEY", "sk-test")
    cfg = _cfg(server)
    cfg["providers"]["local"]["api_key_env"] = "LX_TEST_KEY"
    build("local", cfg).complete("sys", "user")
    assert SEEN["auth"] == "Bearer sk-test"


def test_unknown_provider_names_are_explicit():
    with pytest.raises(ProviderError, match="unknown provider"):
        build("nope", _cfg("http://x/v1"))


def test_unreachable_server_gives_actionable_message():
    # timeout=0.2 rather than 1: port 1 is refused instantly on Linux but times
    # out on Windows, so a one-second timeout bought this message-shape check a
    # second of waiting on the development platform and nothing else.
    p = build("local", _cfg("http://127.0.0.1:1/v1", retries=0, timeout=0.2))
    with pytest.raises(ProviderError, match="base_url"):
        p.complete("s", "u")


def test_the_final_attempt_does_not_sleep(busy):
    """`retries=0` means one attempt and no waiting.

    Every failure path used to sleep unconditionally, including on the last
    attempt, and then leave the loop and raise anyway — a second of latency for
    a retry that was never going to happen, paid by every caller with its
    retries exhausted.
    """
    p = build("local", _cfg(busy, retries=0))
    start = time.perf_counter()
    with pytest.raises(ProviderError, match="HTTP 503"):
        p.complete("s", "u")
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"took {elapsed:.2f}s — a sleep after the final attempt is back"


def test_retry_after_is_honoured_rather_than_the_backoff(rate_limited):
    RATE_LIMITED["calls"] = 0
    p = build("local", _cfg(rate_limited, retries=2))
    start = time.perf_counter()
    out = p.complete("s", "u")
    elapsed = time.perf_counter() - start
    assert out == '{"s1": "ok"}'
    assert RATE_LIMITED["calls"] == 2, "the 429 should have been retried exactly once"
    assert elapsed < 0.5, (
        f"took {elapsed:.2f}s — the backoff for attempt 0 is 1s or more, so "
        "`Retry-After: 0` was ignored")


def test_backoff_is_capped_jittered_and_survives_a_date():
    p = build("local", _cfg("http://x/v1"))
    # Jitter: same attempt, different waits, never below the exponential floor.
    waits = {p._backoff(3) for _ in range(20)}
    assert len(waits) > 1
    assert all(8.0 <= w < 9.0 for w in waits)
    # The ceiling holds whichever side the number comes from.
    assert p._backoff(30) == 20.0
    assert p._backoff(0, "999") == 20.0
    assert p._backoff(0, "-5") == 0.0
    # An HTTP-date is not parsed; it falls through to the backoff we control.
    assert 1.0 <= p._backoff(0, "Wed, 21 Oct 2015 07:28:00 GMT") < 2.0


def test_a_stalled_read_gives_an_actionable_message(stalling):
    """A read timeout must reach the user as advice, not as a bare OSError.

    This can only *fail* on Python 3.9, where `socket.timeout` is its own class
    and the handler used to name only the builtin `TimeoutError`. From 3.10 the
    two are one class and the test passes either way — it is kept as the
    regression guard for the version CI still runs, not as local proof.
    """
    p = build("local", _cfg(stalling, retries=0, timeout=0.3))
    with pytest.raises(ProviderError, match="timed out"):
        p.complete("s", "u")
