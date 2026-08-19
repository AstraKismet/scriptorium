"""The workbench's HTTP surface is written down, and these tests keep it true.

`docs/contracts/workbench-http.md` is the frozen contract. A contract document
that has drifted from the server is worse than no document, because a consumer
implements against it and only finds out at runtime — so nothing here reads the
document for information. Everything is a comparison: the endpoint list in both
directions, the version each side declares, the confinement rule that binds by
field presence rather than by endpoint name, and the four things that are true
only because something is absent and are therefore invisible in a diff.

The extractor reads `web/server.py` with `ast` rather than importing a route
table, because there is no route table — the dispatch is an `if path == …` chain
inside `_get` and `_post`. If that ever changes shape the extractor finds nothing
and `test_the_contract_lists_every_endpoint_the_server_serves` fails on its floor
assertion, which is the intended outcome: whoever restructures the dispatch owns
teaching this file how to read it.
"""

import ast
import hashlib
import http.client
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import scriptorium  # noqa: E402
import scriptorium.cli as cli  # noqa: E402
from scriptorium.web.server import CONTRACT_VERSION, _Handler  # noqa: E402

_ROOT = os.path.join(os.path.dirname(__file__), "..")
CONTRACT = os.path.join(_ROOT, "docs", "contracts", "workbench-http.md")
SERVER_SRC = os.path.join(_ROOT, "src", "scriptorium", "web", "server.py")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _documented():
    """`{(method, path)}` as the contract's own endpoint headings state them."""
    return set(re.findall(r"^### (GET|POST) (/api/\S+)$", _read(CONTRACT), re.M))


def _documented_response_keys():
    """`{(method, path): {key, …}}` from each endpoint section's Response table.

    The document is the source. A response key is what a consumer implements
    against, and the contract's own versioning rule calls renaming or removing
    one a breaking change — so the set has to be compared against a live reply,
    not merely written down beside it.
    """
    text = _read(CONTRACT)
    parts = re.split(r"^### (GET|POST) (/api/\S+)$", text, flags=re.M)
    out = {}
    for i in range(1, len(parts), 3):
        method, path, body = parts[i], parts[i + 1], parts[i + 2]
        after = body.split("**Response**", 1)
        assert len(after) == 2, f"{method} {path} has no Response section"
        # `Side effects:` closes the block. Without a terminator the split would
        # swallow the next endpoint's Request table and the comparison would
        # quietly pass on a superset.
        block = re.split(r"^Side effects:", after[1], flags=re.M)[0]
        keys = set(re.findall(r"^\| `(\w+)` \|", block, flags=re.M))
        assert keys, f"{method} {path} documents no response keys"
        out[(method, path)] = keys
    return out


def _served():
    """`{(method, path)}` as `web/server.py` actually dispatches them.

    Every `/api/…` string constant inside `_get` and `_post`, not only the ones
    in a `==` comparison: a dict-based or tuple-based dispatch would still be
    found, and a literal that is reachable but not compared is a defect this
    should surface rather than skip.
    """
    tree = ast.parse(_read(SERVER_SRC))
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in ("_get", "_post"):
            continue
        method = "GET" if node.name == "_get" else "POST"
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if sub.value.startswith("/api/"):
                    out.add((method, sub.value))
    return out


# Collected at import time so the confinement rule can be parametrized over the
# real endpoint list — an endpoint added to both sides is covered without anyone
# remembering to add a case.
ENDPOINTS = sorted(_documented())


@pytest.fixture(scope="module")
def base():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project root two levels inside `tmp_path`, and the cwd moved into it.

    Two levels because an escape target must still land inside the directory
    pytest owns and rotates — `docs/decisions.md`, 2026-07-29. One level down
    puts it in pytest's shared base, which pytest never cleans, so one run of
    broken code would fail this file forever afterwards.
    """
    root = tmp_path / "nest" / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("# Title\n\nA sentence.\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _request(base, method, path, body=None, headers=None):
    """`(status, body_bytes)`, with a 4xx returned rather than raised."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    head = {"Content-Type": "application/json"} if data else {}
    head.update(headers or {})
    req = urllib.request.Request(base + path, data=data, method=method, headers=head)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _call(base, method, path, payload):
    """One request to `path`, carrying `payload` the way that method carries it."""
    if method == "GET":
        return _request(base, "GET", path + "?" + urllib.parse.urlencode(payload))
    return _request(base, "POST", path, payload)


def _bare(base, method, path):
    """A request by a method `urllib` will not send, returning its status."""
    parts = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.request(method, path)
        r = conn.getresponse()
        r.read()
        return r.status
    finally:
        conn.close()


# ── the endpoint list ──────────────────────────────────────────────────────

def test_the_contract_lists_every_endpoint_the_server_serves():
    served = _served()
    assert len(served) >= 10, (
        f"the extractor found {len(served)} endpoints in {SERVER_SRC}, which cannot be "
        f"right — the dispatch has been restructured and this file's `_served()` no "
        f"longer reads it. Teach it the new shape rather than lowering this floor.")
    undocumented = served - set(ENDPOINTS)
    phantom = set(ENDPOINTS) - served
    assert not undocumented, (
        f"{sorted(undocumented)} are served but absent from {CONTRACT}. Freezing the "
        f"contract means an endpoint reaches the document before it reaches a client.")
    assert not phantom, (
        f"{sorted(phantom)} are in {CONTRACT} but no longer served. A consumer will "
        f"implement them.")


def test_every_cli_function_the_server_stands_in_front_of_is_named_in_the_contract():
    """Invariant 8 made checkable: the seam is stated, not merely intended.

    One direction only. The contract names `cli.do_config_*` under *Reserved*,
    which the server deliberately does not import — asserting the reverse would
    turn writing down a future seam into a test failure.
    """
    tree = ast.parse(_read(SERVER_SRC))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "cli" and node.level == 2:
            imported.update(a.name for a in node.names if a.name.startswith("do_"))
    assert imported, "the server imports no `do_*` function; the extractor is wrong"
    text = _read(CONTRACT)
    for name in sorted(imported):
        assert getattr(cli, name, None) is not None, f"cli.{name} does not exist"
        assert f"cli.{name}" in text, (
            f"the server calls cli.{name} and the contract never names it. An endpoint "
            f"must state which CLI function it stands in front of, or state that it has "
            f"none and why.")


# ── the version ────────────────────────────────────────────────────────────

def test_the_document_declares_exactly_one_contract_version_and_the_module_agrees():
    declared = re.findall(r"^contract_version = (\d+)$", _read(CONTRACT), re.M)
    assert declared == [str(CONTRACT_VERSION)], (
        f"{CONTRACT} declares {declared} and web.server.CONTRACT_VERSION is "
        f"{CONTRACT_VERSION}. There is one version and it is written in both places.")
    # The document states the number **twice**, and the anchored regex above sees
    # only one of them. The other is `/api/state`'s response-table cell, which is
    # what a client implementer reads to learn which number the running server
    # will send — so it is a second declaration, not prose about one. Measured by
    # an adversarial pass on 2026-08-19: reverting that cell alone left the whole
    # suite green while the document disagreed with itself and with the server.
    cell = re.findall(r"^\| `contract_version` \| integer \|[^|]*?`(\d+)`", _read(CONTRACT), re.M)
    assert cell == [str(CONTRACT_VERSION)], (
        f"{CONTRACT}'s `/api/state` response table says {cell}; the fenced declaration "
        f"and web.server.CONTRACT_VERSION say {CONTRACT_VERSION}.")


def test_the_running_server_reports_the_contract_version(base, project):
    code, body = _request(base, "GET", "/api/state")
    assert code == 200
    state = json.loads(body)
    assert state["contract_version"] == CONTRACT_VERSION
    assert isinstance(state["contract_version"], int)
    # Separate fields answering separate questions, from separate sources. A
    # client that read `version` as the contract version would see it move on a
    # release that changed nothing here.
    assert state["version"] == scriptorium.__version__


# ── confinement binds by field presence, not by endpoint name ──────────────

@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_every_endpoint_confines_src(base, project, method, path):
    code, body = _call(base, method, path, {"src": "../../escape.md", "lang": "zh-TW"})
    assert code == 403, f"{method} {path} accepted a src outside the project"
    # The sentence names the field, which is what distinguishes `confined_path`
    # firing from the admission gate refusing for an unrelated reason.
    assert "src =" in json.loads(body)["error"]


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_every_endpoint_whitelists_lang(base, project, method, path):
    code, body = _call(base, method, path, {"src": "docs/guide.md", "lang": "../../pwn"})
    assert code == 403, f"{method} {path} accepted a lang that is not a language tag"
    assert "lang =" in json.loads(body)["error"]


def test_a_json_null_lang_reaches_the_validator_on_a_post(base, project):
    """`dict.get` cannot tell an absent key from a JSON null; `in body` can.

    Measured 2026-07-29: `{"lang": null}` skipped the whitelist entirely and
    `/api/extract` answered 200. Pinned here as well as in `test_web.py` because
    the contract states the asymmetry between the two verbs as normative.
    """
    code, _ = _request(base, "POST", "/api/extract", {"src": "docs/guide.md", "lang": None})
    assert code == 403


# ── the negative space ─────────────────────────────────────────────────────

def test_no_cross_origin_header_is_emitted_anywhere():
    """The absence is load-bearing and invisible in a diff.

    No `Access-Control-Allow-*` is what makes a cross-site `cors`-mode call
    preflight, and the preflight is what fails. A permissive `do_OPTIONS` added
    later would reopen the whole admission gate silently.
    """
    src = _read(SERVER_SRC)
    assert "Access-Control" not in src
    assert "do_OPTIONS" not in src


@pytest.mark.parametrize("method", ["OPTIONS", "PUT", "DELETE", "PATCH", "HEAD"])
def test_a_method_the_contract_does_not_define_is_not_implemented(base, method):
    assert _bare(base, method, "/api/state") == 501


def _headers(base, method, path):
    parts = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.request(method, path)
        r = conn.getresponse()
        r.read()
        return r.status, {k.lower(): v for k, v in r.getheaders()}
    finally:
        conn.close()


def test_every_response_this_server_writes_carries_no_store_and_nothing_else(base, project):
    for path in ("/api/state", "/api/nope", "/"):
        code, head = _headers(base, "GET", path)
        assert head.get("cache-control") == "no-store", f"{path} answered {code}"
        for name in ("etag", "last-modified", "expires", "pragma", "vary",
                     "content-encoding"):
            assert name not in head, f"{name} on {path} is not in the contract"


def test_the_501_is_the_one_response_the_contract_carves_out(base):
    """It is the standard library's, not this server's, and it is not JSON.

    The contract says so explicitly rather than claiming "every response is
    JSON with no-store", because that sentence was false and a client that
    believed it would break on the first stray `OPTIONS`. Pinned so the carve-out
    stays honest: if someone adds a `do_OPTIONS`, this fails and they have to
    read the section that tells them to route it through the admission gate.
    """
    status, head = _headers(base, "OPTIONS", "/api/state")
    assert status == 501
    assert head["content-type"] == "text/html;charset=utf-8"
    assert "cache-control" not in head


def _snapshot(root):
    """Content hashes of every file under `root`, minus the SQLite sidecars.

    `.lx/state.db` and its `-wal` / `-shm` companions are excluded because
    closing the last connection to a WAL database checkpoints it, which rewrites
    the main file for a read — a true statement about SQLite and a false one
    about the endpoint. Everything the claim is actually about is still here: a
    rendered document, a report under `.lx/reports/`, an appended translation
    memory, or a source document modified in place.
    """
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            if os.path.basename(full).startswith("state.db"):
                continue
            with open(full, "rb") as f:
                out[os.path.relpath(full, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


def test_every_endpoint_returns_exactly_the_keys_the_contract_documents(base, project):
    """One valid call to each documented endpoint, compared against the document.

    Without this, the contract's own definition of a breaking change — renaming
    or removing a response key — was the one class of break nothing could see:
    the suite exercised eight of the ten endpoints only through their refusals,
    and never looked at a successful body except `/api/state`'s.

    No network. `/api/translate` is given an `ids` list matching no segment, so
    the job selects nothing and the worker returns before it builds a provider;
    every other endpoint is deterministic.
    """
    doc = {"src": "docs/guide.md", "lang": "zh-TW"}
    code, body = _request(base, "POST", "/api/extract", doc)
    assert code == 200, body
    actual = {("POST", "/api/extract"): set(json.loads(body))}

    def record(method, path, payload=None):
        if method == "GET":
            code, body = _call(base, "GET", path, payload or doc)
        else:
            code, body = _request(base, "POST", path, {**doc, **(payload or {})})
        assert code == 200, f"{method} {path} answered {code}: {body!r}"
        actual[(method, path)] = set(json.loads(body))
        return json.loads(body)

    seen = record("GET", "/api/doc")
    seg = seen["segments"][0]["id"]
    record("POST", "/api/save", {"targets": {seg: "標題"}})
    # After the save, because holding refuses a segment with no target — the
    # order here is the order a reviewer works in, not an arrangement.
    record("POST", "/api/hold", {"ids": [seg]})
    record("POST", "/api/hold", {"ids": [seg], "held": False})
    record("POST", "/api/check")
    record("GET", "/api/preview")
    record("POST", "/api/render")
    record("POST", "/api/commit")
    job = record("POST", "/api/translate", {"ids": ["no-such-segment"]})
    assert job["total"] == 0, "the run must select nothing, or this test needs a network"
    record("POST", "/api/job", {"id": job["id"]})
    record("GET", "/api/state", {})

    documented = _documented_response_keys()
    assert set(documented) == set(actual), "an endpoint was not exercised"
    for key in sorted(actual):
        method, path = key
        assert actual[key] == documented[key], (
            f"{method} {path} answered {sorted(actual[key])} and the contract "
            f"documents {sorted(documented[key])}. Renaming or removing a key is a "
            f"contract_version bump; adding one is an edit to {CONTRACT}.")


def test_an_unknown_job_id_is_a_200_with_one_key(base, project):
    """The contract calls this out as a divergence, so it is pinned as it is.

    Not `404`, not `400`, and not the eight-key shape — a body carrying `error`
    alone. A client that switched on the status code would read it as success.
    """
    code, body = _request(base, "POST", "/api/job", {"id": "job999"})
    assert code == 200
    assert json.loads(body) == {"error": "no such job"}


def test_no_get_endpoint_changes_anything_on_disk(base, project):
    """A GET carries no `Origin`, so the gate has one rule fewer — see the contract.

    `/api/doc` and `POST /api/check` differ by one keyword argument, `persist`,
    and by nothing else that is visible in the source. This is what stops the
    difference from being lost in a rebuild.
    """
    code, _ = _request(base, "POST", "/api/extract", {"src": "docs/guide.md", "lang": "zh-TW"})
    assert code == 200
    before = _snapshot(project)
    for method, path in ENDPOINTS:
        if method != "GET":
            continue
        code, _ = _call(base, "GET", path, {"src": "docs/guide.md", "lang": "zh-TW"})
        assert code == 200, f"GET {path} failed, so this proves nothing about it"
    assert _snapshot(project) == before
    assert not os.path.exists(os.path.join(project, ".lx", "reports")), (
        "a GET wrote a check report; `/api/doc` must call do_check with persist=False")
