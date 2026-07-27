"""The workbench is a shell over the CLI, so these tests only prove the shell holds."""

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium.web.server import _Handler  # noqa: E402


@pytest.fixture(scope="module")
def base():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, r.read()


def test_index_serves(base):
    code, html = _get(base, "/")
    assert code == 200
    assert b"Scriptorium" in html


def test_state_lists_providers(base, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, body = _get(base, "/api/state")
    state = json.loads(body)
    assert code == 200
    assert {"local", "openai", "claude"} <= {p["name"] for p in state["providers"]}


def test_path_traversal_is_refused(base):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/../../../etc/passwd")
    assert e.value.code == 403


def test_unknown_endpoint_reports_cleanly(base):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/api/nope")
    assert e.value.code == 400
