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


@pytest.mark.parametrize("path", [
    # The only spelling the previous guard caught, kept so the fix is not a
    # trade: posixpath.normpath does collapse these.
    "/../../../etc/passwd",
    # It does not treat a backslash as a separator; Windows' open does, so this
    # one passed the guard unchanged and then resolved into the repository root.
    "/x\\..\\..\\..\\..\\..\\pyproject.toml",
    # Decoded before the check, or the check reads inert text.
    "/%2e%2e%2f%2e%2e%2f%2e%2e%2fpyproject.toml",
    "/x%5C..%5C..%5C..%5C..%5C..%5Cpyproject.toml",
])
def test_traversal_is_refused_in_every_spelling(base, path):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, path)
    assert e.value.code == 403


def test_unknown_path_is_404_rather_than_a_silent_index(base):
    # Serving index.html with a 200 made every typo look like a blank app — and
    # would have made a traversal that got through look like it had been served.
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/does-not-exist.js")
    assert e.value.code == 404


def test_percent_encoded_names_still_resolve(base):
    # The other half of unquoting: without it any asset whose name has a space
    # or a non-ASCII character 404s. "/index.html" spelled the hard way.
    code, html = _get(base, "/%69ndex.html")
    assert code == 200
    assert b"Scriptorium" in html


def test_unknown_endpoint_reports_cleanly(base):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/api/nope")
    assert e.value.code == 400
