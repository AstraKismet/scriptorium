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
    p = build("local", _cfg("http://127.0.0.1:1/v1", retries=0, timeout=1))
    with pytest.raises(ProviderError, match="base_url"):
        p.complete("s", "u")


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
