"""The workbench is a shell over the CLI, so these tests only prove the shell holds."""

import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import scriptorium.cli as cli  # noqa: E402
import statedb  # noqa: E402
from scriptorium import translate as translate_mod  # noqa: E402
from scriptorium.cli import UnsafePath, confined_path  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG, get_in, load_config  # noqa: E402
from scriptorium.store import (  # noqa: E402
    SEGMENTATION_VERSION,
    append_tm,
    load_doc,
    slot_originals,
    target_token,
)
from scriptorium.web import server as web_server  # noqa: E402
from scriptorium.web.server import _Handler, _own_hosts, _own_origins  # noqa: E402


def _try_post(base, path, obj):
    """`(status, body)` with a 4xx returned rather than raised."""
    req = urllib.request.Request(
        base + path, data=json.dumps(obj).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture(scope="module")
def base():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _port(base):
    return int(base.rsplit(":", 1)[1])


def _get(base, path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


def _post(base, path, obj, headers=None):
    req = urllib.request.Request(
        base + path, data=json.dumps(obj).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


def _raw(base, method, path, headers, body=b""):
    """One request with the headers spelled out, duplicates included.

    `urllib.request` keeps headers in a dict, so it cannot send the same name
    twice — which is exactly the shape a "read the first one" bug hides in.
    """
    parts = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers:
            conn.putheader(name, value)
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        if body:
            conn.send(body)
        r = conn.getresponse()
        r.read()
        return r.status
    finally:
        conn.close()


def _project(base, tmp_path, monkeypatch, name="guide.md"):
    """A throwaway project holding one extracted document, ready to render.

    The server runs in this process, so `monkeypatch.chdir` moves the project
    the handler sees — the same way `test_state_lists_providers` already works.
    Rendering has to be able to succeed here, or a test that asserts "no file was
    written" would pass for the wrong reason.

    The root sits two levels INSIDE `tmp_path`, never at `tmp_path` itself. The
    escape tests point `out` at the project's parent and grandparent, and with
    the root at `tmp_path` those artifacts land in pytest's shared base
    directory — which pytest rotates but never sweeps, so a file written above
    `tmp_path` outlives the test that produced it and one run of a broken build
    reds every later run of a correct one. Nested, every escape still lands
    inside `tmp_path`, which this test owns and pytest cleans up.
    """
    root = tmp_path / "nest" / "proj"
    root.mkdir(parents=True)
    monkeypatch.chdir(root)
    (root / name).write_text("# Title\n\nOne short sentence.\n", encoding="utf-8")
    code, _ = _post(base, "/api/extract", {"src": name, "lang": "zh-TW"})
    assert code == 200
    return name, root


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


def _sources_project(tmp_path, monkeypatch, name="docs/guide.md"):
    """A project whose one document sits where the default `sources` glob looks.

    `_project` above puts its document at the root, which `docs/**/*.md` does not
    match — so a candidate test written on it would pass with the subtraction
    removed entirely. No `lx.config.json`: `load_config` with no file is the
    scaffolded default, `sources` and `targets` included.
    """
    root = tmp_path / "nest" / "proj"
    (root / name).parent.mkdir(parents=True)
    (root / name).write_bytes(b"# Title\n\nA sentence.\n")
    monkeypatch.chdir(root)
    return root


def test_state_stops_offering_a_document_it_has_already_extracted(base, tmp_path, monkeypatch):
    """Reproduced on the wire 2026-08-13, closed 2026-08-14.

    Extract `docs/guide.md`, ask for the page again, and the file was still in
    the list with an Extract button on it: the already-seen set held
    `docs\\guide.md` from `os.path.relpath` and the candidate key held
    `docs/guide.md` from `.replace(os.sep, "/")`, so the subtraction never fired
    on any platform whose separator is not `/`. Both sides are `store.doc_id`
    now. Over HTTP rather than through the helper, because the defect was
    platform-dependent and this has to be the run that happens on this machine.

    The key is `untracked` at `contract_version = 2`, spelling the command, this
    response key and HANDOFF-203's forthcoming field one way.
    """
    _sources_project(tmp_path, monkeypatch)

    code, body = _get(base, "/api/state")
    assert code == 200
    assert json.loads(body)["untracked"] == [{"source": "docs/guide.md", "lang": "zh-TW"}]

    code, _ = _post(base, "/api/extract", {"src": "docs/guide.md", "lang": "zh-TW"})
    assert code == 200

    state = json.loads(_get(base, "/api/state")[1])
    assert state["untracked"] == []
    # And the other half, which used to be deliberately left open: one body no
    # longer carries two spellings of one identity. `docs[].source` was
    # `os.path.relpath` verbatim — `docs\guide.md` on this machine, beside the
    # `docs/guide.md` above — and both go through `store.doc_label` now. Asserted
    # against the literal rather than `os.path.join`, because the whole point is
    # that the answer no longer depends on the platform.
    assert [(d["source"], d["lang"]) for d in state["docs"]] == [("docs/guide.md", "zh-TW")]


def test_state_reads_every_document_once_to_draw_one_page(base, tmp_path, monkeypatch):
    """`tracked()` loads every segment of every document in the project.

    The candidate scan used to make the same call again to subtract what it
    found, so the bootstrap endpoint paid for the whole project twice before a
    client could draw anything. The fix is that the result is passed on, and the
    property that proves it is a count — a second read is invisible in a
    response.
    """
    _sources_project(tmp_path, monkeypatch)
    code, _ = _post(base, "/api/extract", {"src": "docs/guide.md", "lang": "zh-TW"})
    assert code == 200

    seen = {"server": 0, "cli": 0}
    real = web_server.tracked

    def counting(where):
        def counted(*args, **kwargs):
            seen[where] += 1
            return real(*args, **kwargs)
        return counted

    monkeypatch.setattr(web_server, "tracked", counting("server"))
    monkeypatch.setattr(cli, "tracked", counting("cli"))
    code, body = _get(base, "/api/state")
    assert code == 200 and json.loads(body)["docs"]
    # The total, not which module's global was reached: reading the project once
    # is the property, and routing the call through `cli` instead of `..store` is
    # the direction invariant 8 points. Counted on both sides so a failure says
    # where the reads went.
    assert seen["server"] + seen["cli"] == 1, seen


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


def test_a_drive_absolute_path_cannot_escape(base):
    # A fourth escape shape, and the one where the two platforms genuinely
    # differ: os.path.join discards its first half when the second names a
    # drive, so this leaves the root on Windows and is an ordinary missing
    # relative name on Linux. The status differs, 403 against 404; the property
    # asserted is the one that does not — it is never 200.
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/C:/Windows/win.ini")
    assert e.value.code in (403, 404)


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


# ── the endpoints confine what they open ───────────────────────────────────
#
# "Outside the project" is spelled with `tmp_path`, never with /etc or
# C:\Windows: `monkeypatch.chdir(tmp_path)` makes tmp_path the project, so its
# parent is outside it on both runners and the test means one thing.

def test_render_out_cannot_be_an_absolute_path_outside_the_project(base, tmp_path, monkeypatch):
    # The write primitive: `/api/render` opened whatever `out` named, so any page
    # a browser could be induced to load could drop a file anywhere this user can
    # write. Without the guard this request answers 200 and the file appears —
    # at `tmp_path`, which is outside the project root and inside this test's
    # own temp directory, so a broken build cannot poison a shared one.
    src, root = _project(base, tmp_path, monkeypatch)
    escaped = tmp_path / "escaped-absolute.md"
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, "/api/render",
              {"src": src, "lang": "zh-TW", "fallback": True, "out": str(escaped)})
    assert e.value.code == 403
    assert not escaped.exists()


def test_render_out_cannot_climb_out_with_dotdot(base, tmp_path, monkeypatch):
    # The same primitive spelled relatively, which is the spelling a string check
    # on the leading character misses once the path has anything in front of it.
    # Two levels up from nest/proj is exactly `tmp_path`: still outside the
    # project, still inside pytest's per-test directory.
    src, root = _project(base, tmp_path, monkeypatch)
    escaped = tmp_path / "escaped-relative.md"
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, "/api/render",
              {"src": src, "lang": "zh-TW", "fallback": True, "out": "../../escaped-relative.md"})
    assert e.value.code == 403
    assert not escaped.exists()


def test_a_sibling_directory_sharing_the_prefix_is_still_outside(base, tmp_path, monkeypatch):
    # The docstring's own argument for commonpath over startswith, finally
    # pinned: `proj-evil` starts with `proj`, so a prefix comparison calls the
    # sibling inside and writes the file. Mutation proved the hole — swapping
    # commonpath for startswith left every other test green.
    src, root = _project(base, tmp_path, monkeypatch)
    escaped = root.parent / "proj-evil" / "x.md"
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, "/api/render",
              {"src": src, "lang": "zh-TW", "fallback": True, "out": "../proj-evil/x.md"})
    assert e.value.code == 403
    assert not escaped.exists()


def test_doc_src_cannot_be_an_absolute_path_outside_the_project(base, tmp_path, monkeypatch):
    # The read side. 403 rather than merely "not 200", because a file outside the
    # project has no state either — a missing-state 400 would let this test pass
    # with the confinement removed, and pin nothing. The project root is nested
    # the way `_project` nests it, so the foreign file sits above the project
    # while staying inside this test's own temp directory.
    root = tmp_path / "nest" / "proj"
    root.mkdir(parents=True)
    monkeypatch.chdir(root)
    outside = tmp_path / "outside.md"
    outside.write_text("# Not yours\n", encoding="utf-8")
    query = urllib.parse.urlencode({"src": str(outside), "lang": "zh-TW"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/api/doc?" + query)
    assert e.value.code == 403


def test_extract_src_cannot_reach_outside_the_project(base, tmp_path, monkeypatch):
    # The POST side of the same confinement, which the GET test above cannot
    # pin: `_post` validates `src` on its own line. Mutation proved it unpinned
    # — a build that kept only the null-and-type half of the check answered 200
    # here and created `.lx/docs/.._.._outside.md.zh-TW.json` holding the
    # foreign file's segments. Both spellings, and what the state holds is
    # asserted rather than the status alone, because a 403 sent after the
    # extract would leave the read already done and the state as evidence.
    src, root = _project(base, tmp_path, monkeypatch)
    outside = tmp_path / "outside.md"
    outside.write_text("# Not yours\n\nOne sentence.\n", encoding="utf-8")
    for spelling in (str(outside), "../../outside.md"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(base, "/api/extract", {"src": spelling, "lang": "zh-TW"})
        assert e.value.code == 403
    assert not any("outside" in d["source"] for d in statedb.documents(root))


def test_lang_cannot_walk_out_of_the_state_directory(base, tmp_path, monkeypatch):
    # `lang` is interpolated straight into a filename, so it is a path primitive
    # wearing another name. Measured 2026-07-29, before the whitelist:
    # store_path("guide.md", "../../../../pwn") landed at <project>/pwn.json and
    # tm_path landed a directory above the project. Closing `src` and `out` while
    # leaving this open would have shipped the same write under a different key.
    src, root = _project(base, tmp_path, monkeypatch)
    for endpoint in ("/api/extract", "/api/commit"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(base, endpoint, {"src": src, "lang": "../../../../pwn"})
        assert e.value.code == 403
    # Measured against store.py from a root nested like this one: store_path
    # resolves to <root>/pwn.json and tm_path to <root.parent>/pwn.jsonl — both
    # inside tmp_path, so even the unfixed write cannot outlive the test.
    assert not (root / "pwn.json").exists()
    assert not (root.parent / "pwn.jsonl").exists()


def test_get_lang_cannot_walk_out_of_the_state_directory(base, tmp_path, monkeypatch):
    # The GET half of the same whitelist, which the POST test above cannot pin:
    # `_get` validates `lang` on its own line. 403 exactly, never "non-2xx" —
    # a build with that line deleted still answers 400, because store_path
    # resolves the tag to <root>/pwn.json, *performs* the traversal, and only
    # then fails to find a state file there. The status is the only observable
    # difference between refusing the walk and taking it.
    src, root = _project(base, tmp_path, monkeypatch)
    query = urllib.parse.urlencode({"src": src, "lang": "../../../../pwn"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/api/doc?" + query)
    assert e.value.code == 403
    assert not (root / "pwn.json").exists()


def test_a_json_null_does_not_skip_the_checks(base, tmp_path, monkeypatch):
    # dict.get cannot tell {"lang": null} from an absent key, so a null used to
    # skip the validators entirely — measured: /api/extract answered 200 and
    # created `.lx/docs/guide.md.None.json`, and /api/render wrote and reported
    # `i18n/None/guide.md`. Presence in the body is what routes to the check
    # now; only a genuinely absent key passes, because /api/job sends neither.
    src, root = _project(base, tmp_path, monkeypatch)
    for payload in ({"src": src, "lang": None}, {"src": None, "lang": "zh-TW"}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(base, "/api/extract", payload)
        assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, "/api/render", {"src": src, "lang": None, "fallback": True})
    assert e.value.code == 403
    assert not any(d["lang"] == "None" for d in statedb.documents(root))
    assert not (root / "i18n" / "None").exists()


def test_an_empty_out_still_means_the_default_output(base, tmp_path, monkeypatch):
    # An empty `out` always meant "use the default" — the pre-confinement code
    # was `body.get("out") or default_output(...)` — and the confinement is for
    # non-empty values. Refusing "" would tighten an unrelated semantic under a
    # security flag; a round trip, so the default path is measured, not assumed.
    src, root = _project(base, tmp_path, monkeypatch)
    code, body = _post(base, "/api/render",
                       {"src": src, "lang": "zh-TW", "fallback": True, "out": ""})
    assert code == 200
    assert json.loads(body)["wrote"] == "i18n/zh-TW/guide.md"
    assert (root / "i18n" / "zh-TW" / "guide.md").exists()


@pytest.mark.parametrize("device", ["NUL", "CONOUT$"])
def test_a_reserved_device_name_is_not_an_output_path(base, tmp_path, monkeypatch, device):
    # Measured: open("NUL", "wb") succeeds, os.listdir never shows it, and this
    # request used to answer 200 with {"wrote": "NUL"} while the rendered
    # document went nowhere. CONOUT$ is the same discard through the console —
    # the rendered bytes went to the server's terminal — and one of the two
    # names even 3.13's ntpath.isreserved misses, so the table has to carry it
    # itself. Both resolve inside the project, so containment alone cannot see
    # them — hence a table in front of the resolution.
    src, root = _project(base, tmp_path, monkeypatch)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, "/api/render", {"src": src, "lang": "zh-TW", "fallback": True, "out": device})
    assert e.value.code == 403


def test_an_alternate_data_stream_is_not_an_output_path(base, tmp_path, monkeypatch):
    # The other rule that resolution cannot see: on Windows this writes bytes
    # into a stream of `out.md` and leaves its size and the directory listing
    # unchanged — a covert write onto a file the pipeline treats as input.
    src, root = _project(base, tmp_path, monkeypatch)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, "/api/render",
              {"src": src, "lang": "zh-TW", "fallback": True, "out": "out.md:evil"})
    assert e.value.code == 403


def test_a_drive_relative_output_is_refused_on_both_platforms(base, tmp_path, monkeypatch):
    # "C:foo.md" means "foo.md in whatever directory C: is currently on", which
    # names a different file in every process. Windows refuses it as drive
    # relative and Linux refuses the ':' in the component; one answer either way,
    # because a rule that fires on one runner only is a rule nobody tests.
    src, root = _project(base, tmp_path, monkeypatch)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, "/api/render",
              {"src": src, "lang": "zh-TW", "fallback": True, "out": "C:foo.md"})
    assert e.value.code == 403


def test_a_trailing_dot_is_not_an_output_name(base, tmp_path, monkeypatch):
    # Windows strips a trailing dot or space, so `out.md.` and `out.md` are one
    # file on disk and two names in the API. Measured with the rule neutered:
    # this answered 200 {"wrote": "out.md."} while os.listdir showed `out.md`
    # — the API reporting a filename that does not exist, verbatim the failure
    # the rule's own comment says it exists to stop.
    src, root = _project(base, tmp_path, monkeypatch)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, "/api/render",
              {"src": src, "lang": "zh-TW", "fallback": True, "out": "out.md."})
    assert e.value.code == 403
    assert not (root / "out.md").exists()


def test_a_nul_byte_in_out_is_refused_with_a_name(base, tmp_path, monkeypatch):
    # JSON carries \x00 as \u0000 and the handler's json.loads hands it
    # through, so without the rule the refusal falls all the way to open(),
    # which answers 400 "embedded null character" — a bare CPython internal
    # naming neither the field nor a next action. The rule converts that into
    # a 403 that names `out` and says what to send instead.
    src, root = _project(base, tmp_path, monkeypatch)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, "/api/render",
              {"src": src, "lang": "zh-TW", "fallback": True, "out": "a\x00b.md"})
    assert e.value.code == 403


def test_the_project_directory_itself_is_not_an_output_file(base, tmp_path, monkeypatch):
    # "." resolves to the root, which containment correctly ALLOWS — refusing
    # it there would fail the root's own children — so a dedicated rule turns
    # it into a sentence naming the field. The empty-out test above cannot
    # cover this: "" short-circuits at `if out:` and never reaches the helper.
    # Without the rule this answered 400 "[Errno 13] Permission denied: '.'".
    src, root = _project(base, tmp_path, monkeypatch)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, "/api/render",
              {"src": src, "lang": "zh-TW", "fallback": True, "out": "."})
    assert e.value.code == 403


def test_a_device_or_share_root_refusal_names_what_was_sent():
    # ntpath.splitdrive maps `//./NUL`, `//server/share` and a bare `C:` all to
    # (drive, "") — a root with no file after it. Before the dedicated branch,
    # `//./NUL` fell into the drive-relative refusal, whose advice — "Write it
    # out in full, e.g. //./NUL/docs/guide.md" — cannot be followed. The
    # request is refused either way, so what this pins is the sentence, by a
    # noun phrase rather than its prose. Direct on the helper, because the
    # branch selects a message and HTTP adds nothing to that.
    with pytest.raises(UnsafePath) as e:
        confined_path("//./NUL", "out")
    if os.name == "nt":
        assert "share root" in str(e.value)
    else:
        # posixpath.splitdrive never returns a drive, so off Windows this input
        # is refused by the reserved-device rule and the branch cannot fire;
        # asserting its phrase here would pin a message the platform never
        # produces.
        assert "reserved device" in str(e.value)


def test_a_missing_mandatory_parameter_is_a_400_that_names_it(base, tmp_path, monkeypatch):
    # A missing `src` is not an unsafe path — the caller sent no path at all —
    # so it stays a 400, and the sentence names the field. Both halves are
    # pinned: without `_require` the request falls through to
    # load_doc(None, ...), whose TypeError names NoneType and points nowhere.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/api/doc?lang=zh-TW")
    assert e.value.code == 400
    assert "src" in json.loads(e.value.read())["error"]


# ── the workbench answers its own page and nothing else ────────────────────

def test_render_from_another_origin_is_refused_and_writes_nothing(base, tmp_path, monkeypatch):
    # The whole point of the origin control: a page on any other site could POST
    # here — no CORS preflight is involved in a simple request — and spend money
    # through configured providers or write files, even though it can never read
    # the answer.
    src, root = _project(base, tmp_path, monkeypatch)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, "/api/render", {"src": src, "lang": "zh-TW", "fallback": True},
              headers={"Origin": "http://evil.example"})
    assert e.value.code == 403
    assert not (root / "i18n").exists()


def test_render_without_an_origin_still_writes_the_default_output(base, tmp_path, monkeypatch):
    # The other half, and the one a control like this usually breaks: no Origin
    # at all is curl, `lx`, or an older browser — a local tool, which can write
    # these files without asking anyone. A round trip rather than a status check,
    # so "the workbench still renders to its default output path" is measured
    # rather than assumed.
    src, root = _project(base, tmp_path, monkeypatch)
    code, body = _post(base, "/api/render", {"src": src, "lang": "zh-TW", "fallback": True})
    assert code == 200
    assert json.loads(body)["wrote"] == "i18n/zh-TW/guide.md"
    assert (root / "i18n" / "zh-TW" / "guide.md").exists()


@pytest.mark.parametrize("origin_host", ["127.0.0.1", "localhost", "[::1]"])
def test_render_from_the_workbench_own_page_is_accepted(base, tmp_path, monkeypatch, origin_host):
    # The accepting half of the allowlist, which the refusal tests cannot pin:
    # `serve()` binds 127.0.0.1 and then opens http://localhost:PORT, so the
    # page's own fetches carry the *name* and never the address. Mutation proved
    # the hole — an allowlist of just the bound literal left the whole suite
    # green while every button in the shipped workbench answered 403. Hence the
    # exact browser shape (Origin plus Sec-Fetch-Site: same-origin) for each
    # spelling, and a full round trip rather than a status check, because the
    # failure mode is "the workbench does not work at all".
    src, root = _project(base, tmp_path, monkeypatch)
    code, body = _post(base, "/api/render", {"src": src, "lang": "zh-TW", "fallback": True},
                       headers={"Origin": f"http://{origin_host}:{_port(base)}",
                                "Sec-Fetch-Site": "same-origin"})
    assert code == 200
    assert json.loads(body)["wrote"] == "i18n/zh-TW/guide.md"
    assert (root / "i18n" / "zh-TW" / "guide.md").exists()


def test_origin_null_is_refused(base, tmp_path, monkeypatch):
    # "null" is a present three-byte value, not an absent header: a sandboxed
    # iframe, a data: URL, a file:// page, a cross-origin redirect and an https
    # page posting to this http server all send it. Testing the header for
    # falsiness would accept all five, and an empty Origin with them.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/api/state", headers={"Origin": "null"})
    assert e.value.code == 403


def test_an_empty_origin_is_refused(base, tmp_path, monkeypatch):
    # Present-but-empty is not absent. Membership in the allowlist refuses ""
    # by construction, where the tempting spelling — accept when the header is
    # falsy — waves it through with "null" close behind. Mutation proved this
    # unpinned: `(not origin) or origin in allowed` left every test green. Sent
    # through `_raw`, which puts the empty value on the wire verbatim.
    monkeypatch.chdir(tmp_path)
    status = _raw(base, "GET", "/api/state",
                  [("Host", f"127.0.0.1:{_port(base)}"), ("Origin", "")])
    assert status == 403


def test_origin_on_another_local_port_is_refused(base, tmp_path, monkeypatch):
    # The port is part of the comparison. Another process on another loopback
    # port is a different server, and dropping the port from the check would let
    # anything else running locally drive this one.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/api/state", headers={"Origin": f"http://127.0.0.1:{_port(base) + 1}"})
    assert e.value.code == 403


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_a_fetch_from_another_page_is_refused_on_a_get(base, tmp_path, monkeypatch, site):
    # A GET carries no Origin, so this header is the only thing that can answer
    # "who asked". `same-site` is refused with `cross-site`: a page on another
    # loopback port is same-site and is not this workbench.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/api/state", headers={"Sec-Fetch-Site": site})
    assert e.value.code == 403


def test_a_rebound_host_is_refused(base, tmp_path, monkeypatch):
    # DNS rebinding, which is the case the other two rules cannot see: the
    # browser believes it is same-origin, so it sends no Origin and
    # `Sec-Fetch-Site: same-origin`, and hands the response back to the
    # attacker's script. The name it asked for is the only tell.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/api/state", headers={"Host": f"evil.example:{_port(base)}"})
    assert e.value.code == 403


def test_duplicate_host_headers_are_refused(base, tmp_path, monkeypatch):
    # Mirrors the duplicate-Origin case below: both copies are ours, so a
    # handler that read the first and stopped would answer 200 here — and would
    # do the same when the second copy is the rebound one. Mutation proved the
    # length check unpinned: deleting it left the whole suite green.
    monkeypatch.chdir(tmp_path)
    ours = f"127.0.0.1:{_port(base)}"
    status = _raw(base, "GET", "/api/state", [("Host", ours), ("Host", ours)])
    assert status == 403


def test_duplicate_origin_headers_are_refused(base, tmp_path, monkeypatch):
    # Both copies are ours, so a handler that reads the first one and stops would
    # answer 200 here — and would do the same when the second copy is the one
    # that is not ours. Refuse the pair instead of choosing between them.
    monkeypatch.chdir(tmp_path)
    ours = f"http://127.0.0.1:{_port(base)}"
    status = _raw(base, "GET", "/api/state",
                  [("Host", f"127.0.0.1:{_port(base)}"), ("Origin", ours), ("Origin", ours)])
    assert status == 403


def test_a_cross_site_post_is_refused_on_every_path(base, tmp_path, monkeypatch):
    # The POST gate is unconditional where do_GET scopes to /api/: a POST has
    # no navigation case, so there is no legitimate cross-site POST to any path
    # here. With the gate scoped to the path prefix — the pre-hardening
    # spelling — this request skipped it, ran load_config, and died at the
    # router with 400 "unknown endpoint": the refusal must not depend on the
    # router agreeing, and the status is what tells the two apart.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, "/", {}, headers={"Origin": "http://evil.example"})
    assert e.value.code == 403


def test_the_origin_allowlist_covers_the_spellings_a_browser_sends():
    # The pure helpers, asserted directly: the fixture binds one ephemeral port
    # on one name, so no HTTP test through it can ever see the port-80 rule or
    # a non-loopback bind. A browser omits the port when it is the scheme's
    # default, so without the bare names `lx web --port 80` would 403 every
    # button on its own page — the same class of failure the three-spellings
    # test above was added to close.
    assert "localhost" in _own_hosts(80)
    assert {"127.0.0.1:8787", "localhost:8787", "[::1]:8787"} <= _own_hosts(8787)
    assert _own_origins("0.0.0.0", 8787) is None
    assert {"http://127.0.0.1:8787", "http://localhost:8787",
            "http://[::1]:8787"} <= _own_origins("127.0.0.1", 8787)


def test_the_degraded_origin_check_still_refuses_a_foreign_page(base, tmp_path, monkeypatch):
    # The fixture always binds loopback, so `_own_origins` never returns None
    # and the degraded branch is dead under test — a build whose comparison was
    # replaced with `ok = True`, accepting every Origin on a non-loopback bind,
    # stayed green. The monkeypatch stands in for a non-loopback bind so the
    # suite never listens on a public interface.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(web_server, "_own_origins", lambda host, port: None)
    # Degraded means "did the page come from the name it is addressing": the
    # request's own Host is the only comparison left, so that origin passes...
    code, _ = _get(base, "/api/state",
                   headers={"Origin": f"http://127.0.0.1:{_port(base)}"})
    assert code == 200
    # ...and any other origin is still refused, which is the property the
    # degraded branch exists to keep.
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/api/state", headers={"Origin": "http://evil.example"})
    assert e.value.code == 403


# ── the lost-update token, and what a save may not store ───────────────────
#
# `contract_version = 2`. The surface whose entire purpose is human review had no
# concurrency control of any kind: a background translation job, or a second
# window, overwrote a reviewer's sentence and both requests answered 200.

def _segments(base, name):
    return json.loads(_get(base, f"/api/doc?src={name}&lang=zh-TW")[1])["segments"]


def test_doc_gives_every_segment_the_token_a_save_hands_back(base, tmp_path, monkeypatch):
    name, _root = _project(base, tmp_path, monkeypatch)
    segments = _segments(base, name)
    assert segments, "the fixture must produce at least one segment"
    for s in segments:
        assert s["token"] == target_token(s["target"])
    # Untranslated and empty are one value here, which is what every reader of a
    # target in this project already believes — `checks` reads
    # `seg.get("target") or ""` and both progress counters test truthiness.
    assert segments[0]["token"] == target_token("")


def test_save_refuses_an_empty_target(base, tmp_path, monkeypatch):
    """The acceptance criterion, on the wire: 400, not 200.

    An empty string used to be a legal target that produced
    `{status: "translated", target: ""}` — a segment marked done with nothing in
    it, removed from the draft queue by the act of being cleared.
    """
    name, _root = _project(base, tmp_path, monkeypatch)
    seg = _segments(base, name)[0]
    code, body = _try_post(base, "/api/save",
                           {"src": name, "lang": "zh-TW", "targets": {seg["id"]: ""}})
    assert code == 400
    assert "lx translate" in json.loads(body)["error"], "the refusal says what to do next"
    assert _segments(base, name)[0]["target"] == ""


def test_save_hands_back_the_text_it_stored_and_its_new_token(
        base, tmp_path, monkeypatch):
    """The readback that removes the save-then-refetch-the-whole-book loop."""
    name, _root = _project(base, tmp_path, monkeypatch)
    seg = _segments(base, name)[0]
    code, body = _post(base, "/api/save", {
        "src": name, "lang": "zh-TW", "targets": {seg["id"]: "標題"},
        "base": {seg["id"]: seg["token"]}})
    assert code == 200
    saved = json.loads(body)
    assert (saved["applied"], saved["unknown"], saved["conflicts"]) == (1, [], {})
    assert saved["stored"][seg["id"]]["text"] == "標題"
    assert saved["stored"][seg["id"]]["token"] == target_token("標題")
    # And the token it handed back is the one the next save must send.
    assert _segments(base, name)[0]["token"] == saved["stored"][seg["id"]]["token"]


def test_a_stale_token_is_reported_as_a_conflict_and_writes_nothing(
        base, tmp_path, monkeypatch):
    """Divergence (17): the reviewer's sentence used to lose, silently, at 200.

    Reported in the body rather than as a status, because one request carries
    every dirty segment and a status code cannot say which of them lost.
    """
    name, _root = _project(base, tmp_path, monkeypatch)
    seg = _segments(base, name)[0]
    stale = seg["token"]

    code, _ = _post(base, "/api/save", {
        "src": name, "lang": "zh-TW", "targets": {seg["id"]: "第一版"},
        "base": {seg["id"]: stale}})
    assert code == 200

    code, body = _post(base, "/api/save", {
        "src": name, "lang": "zh-TW", "targets": {seg["id"]: "第二版"},
        "base": {seg["id"]: stale}})
    assert code == 200, "a conflict is a 200 with a body, not an error status"
    answer = json.loads(body)
    assert answer["applied"] == 0 and answer["stored"] == {}
    assert answer["conflicts"][seg["id"]] == {
        "text": "第一版", "token": target_token("第一版")}
    assert _segments(base, name)[0]["target"] == "第一版", "the second write was refused"


def test_a_save_that_sends_no_base_writes_the_way_it_always_did(
        base, tmp_path, monkeypatch):
    """`base` is opt-in per id, which is what keeps `lx apply` and any existing
    client working — the check cannot be something a caller has to know about to
    keep its old behaviour."""
    name, _root = _project(base, tmp_path, monkeypatch)
    seg = _segments(base, name)[0]
    _post(base, "/api/save", {"src": name, "lang": "zh-TW", "targets": {seg["id"]: "甲"}})
    code, body = _post(base, "/api/save",
                       {"src": name, "lang": "zh-TW", "targets": {seg["id"]: "乙"}})
    assert code == 200
    assert json.loads(body)["conflicts"] == {}
    assert _segments(base, name)[0]["target"] == "乙"


def test_a_written_target_makes_the_segment_translated_and_an_absent_one_pending(
        base, tmp_path, monkeypatch):
    """Divergence (14), closed by construction rather than by a second counter.

    `status` is the draft queue's selection predicate. It said "a target was
    written" and every count in this contract said "a target is non-empty"; with
    an empty target refused at the door, the two predicates cannot disagree.
    """
    name, _root = _project(base, tmp_path, monkeypatch)
    before = _segments(base, name)[0]
    assert (before["status"], before["target"]) == ("pending", "")
    _post(base, "/api/save", {"src": name, "lang": "zh-TW", "targets": {before["id"]: "標題"}})
    after = _segments(base, name)[0]
    assert (after["status"], after["target"]) == ("translated", "標題")


@pytest.mark.parametrize("payload,mark", [
    ({"targets": {"s0001": 42}}, "a target is text"),
    ({"targets": {"s0001": ["a"]}}, "a target is text"),
    ({"targets": {"s0001": "x"}, "base": "not-a-map"}, "`base` is a map"),
    ({"targets": {"s0001": "x"}, "base": ["s0001"]}, "`base` is a map"),
])
def test_a_malformed_save_is_refused_with_a_sentence_rather_than_a_traceback(
        base, tmp_path, monkeypatch, payload, mark):
    """Both shapes the adversarial pass reproduced, 2026-08-14.

    A numeric target reached the blank check and raised
    `AttributeError: 'int' object has no attribute 'strip'` — exit 1 and a stack
    trace on the CLI, a 400 quoting a CPython internal on the wire. A `base` sent
    as a string was worse than an error: `sid in base` is a substring test, so the
    lost-update protection was silently switched off for a client that had asked
    for it.
    """
    name, _root = _project(base, tmp_path, monkeypatch)
    seg = _segments(base, name)[0]
    payload = {**payload, "targets": {seg["id"]: list(payload["targets"].values())[0]}}
    code, body = _try_post(base, "/api/save", {"src": name, "lang": "zh-TW", **payload})
    assert code == 400
    message = json.loads(body)["error"]
    assert mark in message
    assert "strip" not in message, "a CPython internal is not a sentence"
    assert _segments(base, name)[0]["target"] == "", "a refused save writes nothing"


def test_state_carries_the_collision_list_even_when_it_is_empty(
        base, tmp_path, monkeypatch):
    """Present and empty, so a client never has to tell "none" from "older build"."""
    _sources_project(tmp_path, monkeypatch)
    state = json.loads(_get(base, "/api/state")[1])
    assert state["collisions"] == []


def test_state_names_the_file_one_identity_swallowed(base, tmp_path, monkeypatch):
    """Divergence (18), on the wire.

    `store.doc_id` flattens every character outside `A-Za-z0-9._-`, so
    `docs/guide.md` and a root-level `docs_guide.md` are one row to
    `.lx/state.db`. Only one can be offered — extracting the second overwrites the
    first — and until now neither surface said which one it had dropped.
    """
    root = _sources_project(tmp_path, monkeypatch)
    # Both inside `docs/`, because the scaffolded `sources` is `docs/**/*.md` and
    # a collision the glob cannot reach proves nothing.
    (root / "docs" / "a").mkdir()
    (root / "docs" / "a" / "b.md").write_bytes(b"# One\n\nA sentence.\n")
    (root / "docs" / "a_b.md").write_bytes(b"# Two\n\nAnother sentence.\n")
    state = json.loads(_get(base, "/api/state")[1])
    assert sorted(u["source"] for u in state["untracked"]) == [
        "docs/a/b.md", "docs/guide.md"]
    assert state["collisions"] == [
        {"paths": ["docs/a/b.md", "docs/a_b.md"], "offered": "docs/a/b.md"}]


# ── which segments a run works on, and which backend it reaches ────────────

def _translate_project(base, tmp_path, monkeypatch):
    """A document in the three states `test_select.py`'s fixture builds.

    Deliberately the same shape, because the claim under test spans the two
    files: the endpoint must select what `cli.do_select` selects, and the way to
    show that is to ask both about a document where the three modes disagree.
    """
    root = tmp_path / "nest" / "proj"
    root.mkdir(parents=True)
    monkeypatch.chdir(root)
    (root / "d.md").write_bytes(
        b"# Title\n\nSee [the guide](https://example.com/here) for details.\n\n"
        b"The gate stood open when she came down the hill.\n\n"
        b"She went in anyway, and it swung shut behind her.\n")
    assert _post(base, "/api/extract", {"src": "d.md", "lang": "zh-TW"})[0] == 200
    ids = [s["id"] for s in json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"]]
    assert _post(base, "/api/save", {"src": "d.md", "lang": "zh-TW",
                                     "targets": {ids[0]: "標題", ids[1]: "請見指南。"}})[0] == 200
    return ids


@pytest.mark.parametrize("mode,want", [
    ("draft", [2, 3]),
    # `/api/save` writes `origin: human`, so segments 0 and 1 are a person's
    # wording and no model run may replace them — selection knows that rule as
    # of 2026-08-16, so neither is offered. Before that, `repair` selected
    # segment 1 and the write refused it, and `polish` selected it on a whole
    # reviewed book and applied none of it.
    ("repair", [2, 3]),
    ("polish", []),
    ("audit", [2, 3]),
])
def test_the_endpoint_selects_what_the_cli_selects(base, tmp_path, monkeypatch,
                                                   mode, want):
    """Divergence (2), asserted on the wire rather than by reading the source.

    `total` is a count of the selection and is fixed at creation, so it is the
    endpoint's own answer to "which segments". Named against the fixture's
    positions rather than against `do_select`'s return value: an oracle that
    calls the code under test moves with it and passes for a mutant that breaks
    both surfaces at once.

    The run itself never reaches a network — every selected segment is banked or
    refused inside the job thread against no provider, and this asserts only the
    number the request answered with.
    """
    _translate_project(base, tmp_path, monkeypatch)
    code, body = _post(base, "/api/translate", {"src": "d.md", "lang": "zh-TW",
                                                "mode": mode})
    assert code == 200
    assert json.loads(body)["total"] == len(want)


def test_ids_outrank_the_mode_on_the_wire_too(base, tmp_path, monkeypatch):
    ids = _translate_project(base, tmp_path, monkeypatch)
    body = json.loads(_post(base, "/api/translate", {
        "src": "d.md", "lang": "zh-TW", "mode": "polish", "ids": [ids[0]]})[1])
    # One, and the id is a segment `polish` would not have offered at all — it is
    # a heading *and* a person's wording. Naming an id is a person pointing at a
    # segment, so it outranks both exclusions; the write still refuses it.
    assert body["total"] == 1
    # An empty array is falsy and falls through to the mode, which the contract
    # states in as many words. `polish` offers nothing on a document written by
    # hand, which is the point of the parametrized test above.
    body = json.loads(_post(base, "/api/translate", {
        "src": "d.md", "lang": "zh-TW", "mode": "polish", "ids": []})[1])
    assert body["total"] == 0


def test_translate_reports_the_route_it_resolved(base, tmp_path, monkeypatch):
    """Divergence (3): the endpoint could not name a model, and did not say which
    one it had picked.

    The readback is the half that makes the field usable — the only other place
    the answer appears is a `log` line the contract forbids parsing, so a
    workbench could not tell a reviewer which model produced the wording in front
    of them. `ids` names nothing, so `total` is 0 and no provider is ever built.
    """
    _translate_project(base, tmp_path, monkeypatch)
    nowhere = {"src": "d.md", "lang": "zh-TW", "ids": ["no-such-segment"]}

    body = json.loads(_post(base, "/api/translate", nowhere)[1])
    assert body["total"] == 0
    assert body["route"] == {
        "provider": "local",
        "model": DEFAULT_CONFIG["providers"]["local"]["model"]}

    # The request's own model outranks the routing entry's and the provider's.
    body = json.loads(_post(base, "/api/translate", {**nowhere, "model": "x:7b"})[1])
    assert body["route"] == {"provider": "local", "model": "x:7b"}

    # A provider naming a different backend drops the entry's model, because a
    # model id belongs to the backend that serves it — and the caller's own
    # survives, because that one was typed for this run and this provider.
    body = json.loads(_post(base, "/api/translate", {**nowhere, "provider": "openai"})[1])
    assert body["route"]["provider"] == "openai"
    body = json.loads(_post(base, "/api/translate",
                            {**nowhere, "provider": "openai", "model": "x:7b"})[1])
    assert body["route"] == {"provider": "openai", "model": "x:7b"}


def test_a_malformed_routing_block_is_reported_in_the_route_not_raised(
        base, tmp_path, monkeypatch):
    """This endpoint's documented behaviour is that a routing problem fails
    *inside the job*, not on the request that starts it.

    Resolving eagerly in order to report the answer back must not quietly turn
    that into a `400` — which is the shape of change the version rule calls a
    bump. `/api/state` degrades the same way and for a neighbouring reason.
    """
    _translate_project(base, tmp_path, monkeypatch)
    (tmp_path / "nest" / "proj" / "lx.config.json").write_text(
        json.dumps({"routing": {"draft": {"provider": ""}}}), encoding="utf-8")
    code, body = _post(base, "/api/translate",
                       {"src": "d.md", "lang": "zh-TW", "ids": ["no-such-segment"]})
    assert code == 200, "a malformed routing entry must not fail the request"
    assert json.loads(body)["route"]["error"]


# ── the job table ──────────────────────────────────────────────────────────

@pytest.fixture
def jobs(monkeypatch):
    """An empty job table, restored afterwards.

    Module-level state in `web.server`, shared by every test in this process —
    including `test_contract.py`'s, which asserts an unknown id's exact body. So
    it is replaced rather than emptied, and the sequence is put back where it
    was: leaving the counter high would make that assertion depend on what ran
    before it, and leaving it low would let this file mint an id that file
    expects to be unknown.
    """
    monkeypatch.setattr(web_server, "_JOBS", {})
    monkeypatch.setattr(web_server, "_JOB_DONE", [])
    monkeypatch.setattr(web_server, "_JOB_SEQ", 0)
    yield


def test_two_jobs_minted_at_once_never_share_an_id(jobs):
    """Divergence (9), reproduced rather than argued.

    `f"job{len(_JOBS) + 1}"` was computed *before* `_JOB_LOCK` was taken, so two
    threads could read the same length, mint the same id, and have the second
    overwrite the first's state — a client polling the id it was handed would
    then be watching someone else's run.

    **This test is the weaker half of the pair and says so**, measured rather
    than assumed: restoring the old minting expression and running this file
    left *this* test green and reddened
    `test_a_job_id_is_never_reused_after_an_eviction` instead, because twenty
    threads under the GIL doing almost no work rarely interleave where it
    matters. The deterministic catch is that one — a length goes backwards the
    moment anything is evicted, so `len(_JOBS)` reissues an id with no race at
    all. Keep both: this one is the only thing watching the lock itself.
    """
    minted, ready = [], threading.Barrier(20)

    def mint():
        ready.wait(timeout=10)
        minted.append(web_server._mint_job(0)["id"])

    threads = [threading.Thread(target=mint) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(minted) == 20
    assert len(set(minted)) == 20, "two runs were handed one id"
    assert sorted(int(i[3:]) for i in minted) == list(range(1, 21))
    assert set(web_server._JOBS) == set(minted), "a state was overwritten"


def test_a_running_job_is_never_evicted_however_many_finish_after_it(jobs):
    """The one rule retention is not allowed to break.

    A record is the only way a client finds out what happened to its run, so the
    bound is on *finished* jobs alone. Asserted with the long-running job minted
    first, which is the order that makes a naive "drop the oldest" wrong.
    """
    running = web_server._mint_job(1)
    for _ in range(web_server._JOB_KEEP + 10):
        web_server._finish_job(web_server._mint_job(0))

    assert running["id"] in web_server._JOBS, "a job still running was dropped"
    assert web_server._job_status(running["id"])["done"] is False
    finished = [k for k, v in web_server._JOBS.items() if v["done"]]
    assert len(finished) == web_server._JOB_KEEP


def test_the_jobs_dropped_are_the_oldest_finished_ones(jobs):
    ids = []
    for _ in range(web_server._JOB_KEEP + 5):
        state = web_server._mint_job(0)
        web_server._finish_job(state)
        ids.append(state["id"])
    assert set(web_server._JOBS) == set(ids[-web_server._JOB_KEEP:])


def test_a_dropped_job_is_a_different_answer_from_one_that_never_existed(base, jobs):
    """What the high-water mark is for.

    Both are still a `200` with `error` alone — divergence (5) stands — but a
    client told "no such job" for a run it watched start has been told something
    false, and the two answers send it to two different places.
    """
    state = web_server._mint_job(0)
    web_server._finish_job(state)
    for _ in range(web_server._JOB_KEEP + 1):
        web_server._finish_job(web_server._mint_job(0))
    assert state["id"] not in web_server._JOBS

    dropped = json.loads(_post(base, "/api/job", {"id": state["id"]})[1])
    assert set(dropped) == {"error"}, "the shape is one key, whatever the sentence"
    assert "dropped" in dropped["error"]

    never = json.loads(_post(base, "/api/job", {"id": "job9999"})[1])
    assert never == {"error": "no such job"}
    # And a spelling that is not a job id at all falls to the same answer rather
    # than to an exception from the parse.
    assert json.loads(_post(base, "/api/job", {"id": "../etc"})[1]) == {
        "error": "no such job"}
    assert json.loads(_post(base, "/api/job", {"id": ""})[1]) == {
        "error": "no such job"}


def test_a_job_id_is_never_reused_after_an_eviction(jobs):
    """The reason the counter is not `len(_JOBS)`.

    A length goes backwards the moment anything is evicted, so the id after a
    drop would collide with one already handed out — which is how retention and
    id-uniqueness turned out to be one defect rather than two.
    """
    seen = set()
    for _ in range(web_server._JOB_KEEP * 2):
        state = web_server._mint_job(0)
        web_server._finish_job(state)
        assert state["id"] not in seen, f"{state['id']} was minted twice"
        seen.add(state["id"])


def test_a_long_run_that_finishes_last_is_not_the_first_thing_evicted(jobs):
    """Why the retention key is completion order and not mint order.

    An hour-long chapter is minted first and finishes last. Under "drop the
    oldest to *start*" it becomes the eviction candidate the instant it
    completes, so its client polls once, is told the record was dropped, and
    never learns whether the run succeeded — the one moment the record exists
    for. Completion order puts the run that just ended at the back of the
    window, which is the safest place in it.
    """
    long_run = web_server._mint_job(1)
    for _ in range(web_server._JOB_KEEP):
        web_server._finish_job(web_server._mint_job(0))
    assert len(web_server._JOB_DONE) == web_server._JOB_KEEP

    web_server._finish_job(long_run)
    status = web_server._job_status(long_run["id"])
    assert status["done"] is True, "the record was gone the moment the run ended"
    assert status["id"] == long_run["id"]


# ── hold, and origin precedence on the wire ────────────────────────────────

def test_a_segment_carries_its_review_state_and_hold_sets_it(base, tmp_path, monkeypatch):
    """`review` is always present rather than omitted when absent, so a client
    does not have to tell "not held" from "an older server" — the rule
    `collisions` already follows on `/api/state`."""
    ids = _translate_project(base, tmp_path, monkeypatch)
    segs = json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"]
    assert all("review" in s for s in segs)
    assert all(s["review"] is None for s in segs)

    code, body = _post(base, "/api/hold", {"src": "d.md", "lang": "zh-TW",
                                           "ids": [ids[1], "nope"]})
    assert code == 200
    assert json.loads(body) == {"applied": 1, "unknown": ["nope"]}

    segs = {s["id"]: s for s in
            json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"]}
    assert segs[ids[1]]["review"] == "held"
    assert [i["rule"] for i in segs[ids[1]]["issues"] if i["severity"] == "warn"] == ["held"]

    code, body = _post(base, "/api/hold", {"src": "d.md", "lang": "zh-TW",
                                           "ids": [ids[1]], "held": False})
    assert json.loads(body)["applied"] == 1
    segs = {s["id"]: s for s in
            json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"]}
    assert segs[ids[1]]["review"] is None


def test_holding_a_segment_with_no_target_is_a_400_naming_the_way_forward(
        base, tmp_path, monkeypatch):
    ids = _translate_project(base, tmp_path, monkeypatch)
    code, body = _try_post(base, "/api/hold",
                           {"src": "d.md", "lang": "zh-TW", "ids": [ids[2]]})
    assert code == 400
    assert "lx translate" in json.loads(body)["error"]
    # And nothing was written, which is what makes the refusal whole-request.
    segs = {s["id"]: s for s in
            json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"]}
    assert segs[ids[2]]["review"] is None


def test_a_save_does_not_release_a_hold_on_the_wire_either(base, tmp_path, monkeypatch):
    """Lifting is the hold control's own act and never a side effect of a save."""
    ids = _translate_project(base, tmp_path, monkeypatch)
    _post(base, "/api/hold", {"src": "d.md", "lang": "zh-TW", "ids": [ids[1]]})
    _post(base, "/api/save", {"src": "d.md", "lang": "zh-TW",
                              "targets": {ids[1]: "改過的字。"}})
    segs = {s["id"]: s for s in
            json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"]}
    assert segs[ids[1]]["target"] == "改過的字。"
    assert segs[ids[1]]["review"] == "held", "the save released the hold"


def test_a_job_reports_the_segments_it_left_alone(base, tmp_path, monkeypatch, jobs):
    """Origin precedence, on the surface a reviewer actually watches.

    `/api/save` writes `human`, so the two segments this project starts with are
    a person's. A run that selects them writes neither and says which — reported
    in `refused` rather than only in a `log` line this contract forbids parsing,
    which is the same mistake divergence (3) was about.

    No network: the provider is stubbed, which works here because this server
    runs in *this* process — the job thread picks up the patched factory. It has
    to be stubbed rather than avoided: the guard fires at the write, so the model
    is asked first, and an unstubbed run spends its retry budget against a
    backend nobody is running. The job is polled until it is done, never slept on.
    """
    class _Echo:
        def describe(self):
            return "stub"

        def complete(self, system, user):
            items = json.loads(user[user.index("["):])
            return json.dumps(
                {i["id"]: "潤過的字。" + "".join(re.findall(r"⟦\d+⟧", i["text"]))
                 for i in items}, ensure_ascii=False)

    monkeypatch.setattr(translate_mod, "build_provider",
                        lambda name, cfg, model=None: _Echo())
    ids = _translate_project(base, tmp_path, monkeypatch)
    # Through `ids`, because that is now the only way a run reaches a segment a
    # person wrote: every *mode* excludes one. The exemption is the design — a
    # named id is a person pointing at a segment — and the write refusing it
    # anyway is what this test is about.
    started = json.loads(_post(base, "/api/translate", {
        "src": "d.md", "lang": "zh-TW", "ids": [ids[1]]})[1])
    assert started["total"] == 1, "an explicit id must still reach the segment"

    for _ in range(200):
        job = json.loads(_post(base, "/api/job", {"id": started["id"]})[1])
        if job["done"]:
            break
        time.sleep(0.05)
    assert job["done"], "the job never finished"
    assert job["applied"] == 0
    assert job["refused"] == [ids[1]]
    segs = {s["id"]: s for s in
            json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"]}
    assert segs[ids[1]]["target"] == "請見指南。"
    assert segs[ids[1]]["origin"] == "human"


# ── the register a reset has to name, and what the reply reports ────────────

def _extract(base, **body):
    """`POST /api/extract`, decoded. Raises `HTTPError` on a refusal, as `_post` does."""
    return json.loads(_post(base, "/api/extract",
                            {"src": "d.md", "lang": "zh-TW", **body})[1])


def _doc_project(base, tmp_path, monkeypatch, text):
    root = tmp_path / "nest" / "proj"
    root.mkdir(parents=True)
    monkeypatch.chdir(root)
    (root / "d.md").write_bytes(text)
    return root


def test_a_reset_that_names_no_register_is_a_400_naming_the_field(
        base, tmp_path, monkeypatch):
    """Version 3's one item, on the wire.

    The status code is half of it; the sentence is the other half. A client
    shown "missing field" sends the field, and the field it will guess is the
    configured default — which is the defect. The body carries `error` and
    nothing else, the way every refusal on this surface does.
    """
    _doc_project(base, tmp_path, monkeypatch, b"One short sentence.\n")
    _extract(base, tone="literary")

    with pytest.raises(urllib.error.HTTPError) as e:
        _extract(base, reset=True)
    assert e.value.code == 400
    body = json.loads(e.value.read())
    assert set(body) == {"error"}
    assert "tone" in body["error"] and "register" in body["error"]

    # And the register is still what it was: a refused request writes nothing.
    assert json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["tone"] == "literary"
    assert _extract(base, reset=True, tone="literary")["segments"] == 1


@pytest.mark.parametrize("reset", [True, 1, "yes", [0]])
def test_anything_truthy_is_a_reset_and_still_has_to_name_the_register(
        reset, base, tmp_path, monkeypatch):
    """`body.get("reset", False)` is passed through unvalidated, so the guard
    reads truthiness rather than identity. Written `if reset is True`, every one
    of these walks around the refusal and lands on the silent default."""
    _doc_project(base, tmp_path, monkeypatch, b"One short sentence.\n")
    _extract(base, tone="literary")
    with pytest.raises(urllib.error.HTTPError) as e:
        _extract(base, reset=reset)
    assert e.value.code == 400


def test_the_three_arrays_are_present_and_empty_on_a_first_extract(
        base, tmp_path, monkeypatch):
    """Present, not conditional: a client must never have to tell "none" from
    "an older server". `collisions` on `/api/state` already sets that rule."""
    _doc_project(base, tmp_path, monkeypatch, b"One short sentence.\n")
    r = _extract(base)
    assert (r["kept"], r["ambiguous"], r["replaced"]) == ([], [], [])


def test_the_reply_names_a_stored_wording_it_kept_but_could_not_accept(
        base, tmp_path, monkeypatch):
    """`kept` — divergence (24)'s population, projected.

    The segment comes back `translated` and failing, which `POST /api/doc`
    already shows; this array is what saves the second call.
    """
    _doc_project(base, tmp_path, monkeypatch,
                 b"See [the guide](https://example.com/here) for details.\n")
    sid = _extract(base) and json.loads(
        _get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"][0]["id"]
    # One slot in the source, two in the wording: no seating can say which of the
    # two the placeholder belongs to, so the acceptance path refuses it.
    _post(base, "/api/save", {"src": "d.md", "lang": "zh-TW",
                              "targets": {sid: "請見 ⟦1⟧ 與 ⟦1⟧。"}})

    r = _extract(base)
    assert (r["kept"], r["rejected"], r["reused"]) == ([sid], 1, 0)
    assert r["replaced"] == [] and r["ambiguous"] == []
    seg = json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"][0]
    assert seg["target"] == "請見 ⟦1⟧ 與 ⟦1⟧。" and seg["origin"] == "human"


def test_the_reply_names_a_segment_the_carryover_could_not_place(
        base, tmp_path, monkeypatch):
    """`ambiguous` — divergence (26)'s population, projected.

    A sentence the document already held, written again: it matches the key of
    one that exists, nothing establishes which, and the fallback hands it the
    last stored wording under that key. Named rather than guessed at in silence.
    """
    root = _doc_project(base, tmp_path, monkeypatch, b"Yes.\n\nMiddle.\n\nYes.\n")
    _extract(base)
    ids = [s["id"] for s in
           json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"]]
    _post(base, "/api/save", {"src": "d.md", "lang": "zh-TW",
                              "targets": {ids[0]: "好。", ids[1]: "中間。", ids[2]: "是的。"}})

    (root / "d.md").write_bytes(b"Yes.\n\nYes.\n\nMiddle.\n\nYes.\n")
    r = _extract(base)
    assert r["ambiguous"] == ["s0001"], "the new occurrence is the one nothing places"
    assert r["kept"] == [] and r["replaced"] == []


def test_the_reply_names_a_wording_the_memory_answered_over(
        base, tmp_path, monkeypatch):
    """`replaced` — divergence (27)'s population, and the only one invisible
    without it.

    Narrowed on 2026-09-01 to the machine drafts, which is why the stale target
    here is written with `cli.do_apply` and an `llm:draft` origin rather than
    through `POST /api/save`: that endpoint writes `human`, and a person's
    wording is kept now rather than replaced — the case below this one.

    The segment still comes back `translated`, passes every validator, and its
    `origin` has rolled to `tm`. Unlike `kept` there is no error on
    `POST /api/doc` to find it by, which is why this array is the one that
    matters most on this reply.
    """
    _doc_project(base, tmp_path, monkeypatch,
                 b"See [the guide](https://example.com/here) for details.\n")
    _extract(base)
    seg = load_doc("d.md", "zh-TW")["segments"][0]
    # A banked wording that fits, sitting behind a stored one that does not.
    append_tm("zh-TW", [{"hash": seg["hash"], "context": seg["context"],
                         "segmentation_version": SEGMENTATION_VERSION,
                         "source": seg["source"], "target": "請見 ⟦1⟧。",
                         "slots": slot_originals(seg["slots"])}])
    cli.do_apply("d.md", "zh-TW", dict(DEFAULT_CONFIG),
                 {seg["id"]: "請見 ⟦1⟧ 與 ⟦1⟧。"}, origin="llm:draft")

    r = _extract(base)
    assert r["replaced"] == [seg["id"]]
    assert r["kept"] == [], "the memory answered, so nothing had to be kept"
    after = json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"][0]
    assert after["target"] == "請見 ⟦1⟧。" and after["origin"] == "tm"


def test_a_wording_saved_here_is_kept_rather_than_answered_over(
        base, tmp_path, monkeypatch):
    """The other side of the same split, over the wire.

    `POST /api/save` writes `human`, so everything a workbench user types is on
    this side: a banked wording behind it no longer wins, however well it fits.
    The segment comes back in `kept` instead, holding the words that were typed,
    and `POST /api/doc` carries the placeholder error on the segment itself.
    """
    _doc_project(base, tmp_path, monkeypatch,
                 b"See [the guide](https://example.com/here) for details.\n")
    _extract(base)
    seg = load_doc("d.md", "zh-TW")["segments"][0]
    append_tm("zh-TW", [{"hash": seg["hash"], "context": seg["context"],
                         "segmentation_version": SEGMENTATION_VERSION,
                         "source": seg["source"], "target": "請見 ⟦1⟧。",
                         "slots": slot_originals(seg["slots"])}])
    _post(base, "/api/save", {"src": "d.md", "lang": "zh-TW",
                              "targets": {seg["id"]: "請見 ⟦1⟧ 與 ⟦1⟧。"}})

    r = _extract(base)
    assert r["kept"] == [seg["id"]] and r["replaced"] == []
    after = json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"][0]
    assert after["target"] == "請見 ⟦1⟧ 與 ⟦1⟧。" and after["origin"] == "human"


def test_commit_names_what_it_declined_to_bank(base, tmp_path, monkeypatch):
    """`refused` and `held` — what `POST /api/commit` did not put in the memory.

    Both are new on 2026-09-01 and both are additive. The count alone was a
    report nobody could act on the moment anything but "has a target" decided
    what gets banked, which is `store.save_targets`' own argument for returning
    its refusals.
    """
    _doc_project(base, tmp_path, monkeypatch,
                 b"See [the guide](https://example.com/here) for details.\n\nPlain.\n")
    _extract(base)
    ids = [s["id"] for s in
           json.loads(_get(base, "/api/doc?src=d.md&lang=zh-TW")[1])["segments"]]
    _post(base, "/api/save", {"src": "d.md", "lang": "zh-TW",
                              "targets": {ids[0]: "請見 ⟦1⟧ 與 ⟦1⟧。", ids[1]: "純文字。"}})
    _post(base, "/api/hold", {"src": "d.md", "lang": "zh-TW",
                              "ids": [ids[1]], "held": True})

    body = json.loads(_post(base, "/api/commit", {"src": "d.md", "lang": "zh-TW"})[1])
    assert body["refused"] == [ids[0]], "a `tags` error is not banked"
    assert body["held"] == [ids[1]]
    assert body["stranded"] == []
    assert body["committed"] == 0


# ── configuration over the wire ────────────────────────────────────────────
#
# The gate itself — which keys, and the acknowledgement — is `cli.writable_key`
# and is tested in `test_config.py`. What is left here is the shell: that the
# refusals reach the wire as the statuses the contract documents, that a refused
# request writes nothing, and that the reply carries what a settings screen
# redraws from without carrying anything invariant 6 keeps off this surface.


def _config_project(tmp_path, monkeypatch):
    """An empty project root, two levels inside `tmp_path`, and the cwd moved in.

    Nested for `_project`'s reason: nothing here escapes, but a sibling test in
    this file points paths at the parent, and a root at `tmp_path` puts those in
    pytest's shared base, which it rotates and never sweeps.
    """
    root = tmp_path / "nest" / "proj"
    root.mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


def _bytes(root):
    path = root / "lx.config.json"
    return path.read_bytes() if path.exists() else None


def test_config_writes_a_key_and_the_cli_reads_the_same_value_back(
        base, tmp_path, monkeypatch):
    """Invariant 8 on this endpoint: one writer, and both surfaces see one file."""
    _config_project(tmp_path, monkeypatch)
    code, body = _post(base, "/api/config", {"key": "batch.size", "value": 12})
    assert code == 200
    assert json.loads(body)["value"] == 12
    assert cli.do_config_get(load_config(), "batch.size") == "12"


@pytest.mark.parametrize("key", [
    "glossary", "dnt", "style", "output_pattern", "sources",
    "providers.local.headers", "providers.local.headers.Authorization",
    "providers", "providers.local", "routing", "routing.draft.model",
    "batch.size.x",
])
def test_a_key_not_writable_over_http_is_a_403_that_writes_nothing(
        base, tmp_path, monkeypatch, key):
    """403 rather than 400: a control refused it, and no value would help.

    The file is compared byte for byte afterwards rather than parsed, because
    "nothing changed" has to include the formatting and the trailing newline —
    `dump_json` rewrites the whole file, so a write that reached it and then
    failed would still show as a diff.
    """
    root = _config_project(tmp_path, monkeypatch)
    assert _post(base, "/api/config", {"key": "batch.size", "value": 8})[0] == 200
    before = _bytes(root)
    code, body = _try_post(base, "/api/config", {"key": key, "value": "anything"})
    assert code == 403, f"{key} was not refused"
    assert "not writable over HTTP" in json.loads(body)["error"]
    assert _bytes(root) == before


@pytest.mark.parametrize("block", [
    {"api_key_env": "sk-REDACTED-LOOKING-VALUE"},
    {"headers": {"Authorization": "Bearer sk-REDACTED-LOOKING-VALUE"}},
])
def test_a_provider_block_is_refused_by_its_key_before_its_contents_matter(
        base, tmp_path, monkeypatch, block):
    """The where-it-lands case, and on this surface it is unreachable.

    `lx config set providers.local '{"api_key_env": …}'` reaches
    `_field_api_key_env` through `_validated`'s descent, which is what makes the
    CLI safe. Here the block form never gets that far: `providers.local` is two
    segments and every admitted provider pattern is three, so the request is
    refused without the value being looked at — and the credential in it is
    therefore not in the reply either.
    """
    root = _config_project(tmp_path, monkeypatch)
    before = _bytes(root)
    code, body = _try_post(base, "/api/config", {"key": "providers.local", "value": block})
    assert code == 403
    assert "sk-REDACTED-LOOKING-VALUE" not in body.decode("utf-8")
    assert _bytes(root) == before


def test_a_key_shaped_api_key_env_is_refused_and_is_nowhere_in_the_reply(
        base, tmp_path, monkeypatch):
    """400, not 403: the key is writable and this value is not.

    The second assertion is the one that matters. `api_key_env` is the field a
    pasted credential lands in, and a refusal that quoted it would have published
    it to whatever renders the sentence — which, since this endpoint exists, is a
    browser rather than a terminal.
    """
    root = _config_project(tmp_path, monkeypatch)
    pasted = "sk-REDACTED-LOOKING-VALUE-0123456789"
    code, body = _try_post(base, "/api/config",
                           {"key": "providers.local.api_key_env", "value": pasted})
    assert code == 400
    assert pasted not in body.decode("utf-8")
    assert "NAME of an environment variable" in json.loads(body)["error"]
    assert _bytes(root) is None


def test_a_bare_routing_string_is_written_bare_over_the_wire_too(
        base, tmp_path, monkeypatch):
    """`lx routing set draft local` writes a bare string and so does this.

    Every configuration on disk uses the bare form and `AGENTS.md` records that
    it is never migrated, so a screen that always emitted the object form would
    rewrite every one of them. It does not have to know: an object naming no
    model comes back bare from the same validator.
    """
    root = _config_project(tmp_path, monkeypatch)
    assert _post(base, "/api/config", {"key": "routing.draft", "value": "local"})[0] == 200
    assert json.loads((root / "lx.config.json").read_text())["routing"]["draft"] == "local"
    assert _post(base, "/api/config",
                 {"key": "routing.polish", "value": {"provider": "local"}})[0] == 200
    assert json.loads((root / "lx.config.json").read_text())["routing"]["polish"] == "local"
    code, body = _post(base, "/api/config",
                       {"key": "routing.repair", "value": {"provider": "local", "model": "m"}})
    assert json.loads(body)["value"] == {"provider": "local", "model": "m"}


@pytest.mark.parametrize("payload", [
    {"value": "http://127.0.0.1:9/v1"},
    {"unset": True},
])
def test_a_base_url_is_not_changed_without_the_acknowledgement(
        base, tmp_path, monkeypatch, payload):
    """A removal changes it too, which is why the rule is keyed on the landing.

    Dropping a key that shadowed a shipped provider's `base_url` restores the
    factory URL; dropping a user-created provider's leaves the spec without one
    and the request falls back to a hardcoded `localhost:11434`. Both are "the
    document now goes somewhere else" with the provider's credential attached.
    """
    root = _config_project(tmp_path, monkeypatch)
    assert _post(base, "/api/config", {"key": "providers.local.base_url",
                                       "value": "http://127.0.0.1:8088/v1",
                                       "confirm_base_url": True})[0] == 200
    before = _bytes(root)
    code, body = _try_post(base, "/api/config",
                           {"key": "providers.local.base_url", **payload})
    assert code == 400, "a missing acknowledgement is fixable by resending, so it is not 403"
    assert "confirm_base_url" in json.loads(body)["error"]
    assert _bytes(root) == before
    assert _post(base, "/api/config",
                 {"key": "providers.local.base_url", **payload,
                  "confirm_base_url": True})[0] == 200
    assert _bytes(root) != before


def test_the_reply_never_carries_a_hand_edited_base_url_in_full(base, tmp_path, monkeypatch):
    """The reply is a display surface, so every projection in it is masked.

    `lx config set` refuses to write a `?key=`, but a file somebody edited can
    hold one — and two surfaces over one value must not disagree about what is
    printable. Measured on 2026-08-13 as the defect that made `lx providers` mask
    what `lx translate` printed in full.

    This exercises the `providers` projection specifically, which is where the
    secret would surface here: the *readback* is of `model`, and the readback
    could not carry one anyway, because the validator that just ran refuses
    exactly what the projection masks. `do_config_value`'s own masking is
    therefore belt and braces on this path and is tested at its own level, in
    `test_config.py` — a mutation run is what said so, by leaving the projection
    in `do_config_value` unkilled while this test went on passing.
    """
    root = _config_project(tmp_path, monkeypatch)
    (root / "lx.config.json").write_text(json.dumps(
        {"providers": {"gw": {"kind": "openai",
                              "base_url": "https://gw.example.com/v1?key=SEKRIT"}}}),
        encoding="utf-8")
    code, body = _post(base, "/api/config", {"key": "providers.gw.model", "value": "m"})
    assert code == 200
    assert "SEKRIT" not in body.decode("utf-8")
    assert "gw.example.com" in body.decode("utf-8")


@pytest.mark.parametrize("field", ["unset", "confirm_base_url"])
@pytest.mark.parametrize("bad", ["false", "true", 1, [], {}])
def test_a_boolean_sent_as_anything_else_is_refused_rather_than_read(
        base, tmp_path, monkeypatch, field, bad):
    """`bool("false")` is `True`, and one of these two removes a key.

    Contract divergence (28) records the same shape one endpoint over, where
    `{"reset": "false"}` discards a document's translations. This endpoint does
    not repeat it.
    """
    root = _config_project(tmp_path, monkeypatch)
    assert _post(base, "/api/config", {"key": "batch.size", "value": 8})[0] == 200
    before = _bytes(root)
    code, body = _try_post(base, "/api/config",
                           {"key": "batch.size", "value": 9, field: bad})
    assert code == 400
    assert field in json.loads(body)["error"]
    assert _bytes(root) == before


def test_a_write_and_a_removal_in_one_request_is_refused_rather_than_ordered(
        base, tmp_path, monkeypatch):
    """Two instructions, no defensible order, so neither is guessed at.

    Reading the `unset` and dropping the value would delete a key the caller had
    just asked to set — the destructive branch of an ambiguity, chosen silently.
    """
    root = _config_project(tmp_path, monkeypatch)
    assert _post(base, "/api/config", {"key": "batch.size", "value": 8})[0] == 200
    before = _bytes(root)
    code, body = _try_post(base, "/api/config",
                           {"key": "batch.size", "value": 9, "unset": True})
    assert code == 400
    assert "not both" in json.loads(body)["error"]
    assert _bytes(root) == before


def test_a_present_null_is_a_value_and_an_absent_one_is_a_refusal(
        base, tmp_path, monkeypatch):
    """`dict.get` cannot tell them apart and this surface has been bitten before.

    A JSON `null` is a real value for `api_key_env` — it means this backend needs
    no key — so it has to reach the validator, while a request that simply left
    the field out has said nothing and must be refused rather than read as one.
    """
    _config_project(tmp_path, monkeypatch)
    code, body = _post(base, "/api/config",
                       {"key": "providers.local.api_key_env", "value": None})
    assert code == 200 and json.loads(body)["value"] == ""
    code, body = _try_post(base, "/api/config", {"key": "providers.local.api_key_env"})
    assert code == 400
    assert "unset: true" in json.loads(body)["error"]


def test_the_reply_carries_the_resolved_routing_a_screen_has_to_render(
        base, tmp_path, monkeypatch):
    """Never `cfg["routing"]`, which is two shapes.

    A page assigning the object form to a `<select>` value gets
    `[object Object]`, shows nothing, and the run goes to whichever backend
    happened to be first. The reply carries `/api/state`'s own projection so the
    screen never has to choose — and carries it at all so that redrawing a form
    does not cost a `/api/state`, which loads every segment of every document.
    """
    _config_project(tmp_path, monkeypatch)
    code, body = _post(base, "/api/config",
                       {"key": "routing.draft", "value": {"provider": "local", "model": "m"}})
    reply = json.loads(body)
    assert code == 200
    assert reply["routing"]["draft"] == {"provider": "local", "model": "m"}
    assert {"name", "kind", "model", "base_url", "needs_key", "key_present", "key_env"} <= set(
        reply["providers"][0])


@pytest.mark.parametrize("providers", [["local"], "local", {"local": "oops"}])
def test_a_malformed_providers_block_no_longer_empties_the_bootstrap_endpoint(
        base, tmp_path, monkeypatch, providers):
    """Contract divergence (15), closed — and this is why it had to be.

    `/api/state` is the endpoint a client calls before it can do anything, and it
    was answering `400` with nothing in it on a configuration that could only be
    repaired by opening the file. Now that it can be repaired over HTTP, the
    repair runs through this same projection: an `available()` that still raised
    would have failed *after* the write landed.
    """
    root = _config_project(tmp_path, monkeypatch)
    (root / "lx.config.json").write_text(json.dumps({"providers": providers}),
                                         encoding="utf-8")
    code, body = _get(base, "/api/state")
    assert code == 200
    state = json.loads(body)
    assert isinstance(state["providers"], list)
    reported = [p.get("error") for p in state["providers"]] or [
        stage.get("error") for stage in state["routing"].values()]
    assert any(reported), "a broken configuration must say so somewhere on this reply"
    code, body = _post(base, "/api/config", {"key": "batch.size", "value": 4})
    assert code == 200, "the endpoint that repairs configuration must run on a broken one"


def test_two_writes_at_once_do_not_lose_each_other(base, tmp_path, monkeypatch):
    """A settings form saving several fields is the default case, not a race.

    Each request reads the file, sets one key and writes it back, so without
    `_CONFIG_LOCK` the last writer wins and the others revert silently. This is
    the weaker kind of test and says so: under the GIL the interleaving is not
    guaranteed, and it is kept because the failure it guards is silent. Removing
    the lock reds it.
    """
    _config_project(tmp_path, monkeypatch)
    keys = ["batch.size", "batch.concurrency", "batch.max_repair_rounds",
            "batch.context", "providers.local.model", "providers.local.timeout",
            "providers.local.retries", "providers.local.temperature"]
    values = [3, 4, 5, 6, "m", 90, 2, 1]
    ready = threading.Barrier(len(keys))
    results = {}

    def write(key, value):
        ready.wait(timeout=10)
        code, body = _try_post(base, "/api/config", {"key": key, "value": value})
        results[key] = code if code == 200 else json.loads(body)["error"]

    threads = [threading.Thread(target=write, args=pair) for pair in zip(keys, values)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert set(results.values()) == {200}, results
    stored = load_config()
    for key, value in zip(keys, values):
        assert get_in(stored, key.split(".")) == value, f"{key} was lost"


# ── the block map and the sentence rule ────────────────────────────────────

def test_preview_carries_the_block_map_and_keeps_missing_an_integer(
        base, tmp_path, monkeypatch):
    """`missing` is documented as "a count, not a list" and must stay one.

    Turning it polymorphic, or replacing it with a list, is a type change and
    bumps the contract version. The list form is `blocks`, which is why both are
    asserted here in one place: a future change that grows the count into a list
    has to red this test on its way past.
    """
    src, _root = _project(base, tmp_path, monkeypatch)
    code, body = _get(base, f"/api/preview?src={src}&lang=zh-TW")
    assert code == 200
    got = json.loads(body)
    assert isinstance(got["missing"], int) and not isinstance(got["missing"], bool)
    assert isinstance(got["blocks"], list) and got["blocks"]
    assert "".join(b["text"] for b in got["blocks"]) == got["text"]
    for block in got["blocks"]:
        assert set(block) == {"id", "kind", "from", "text"}
    assert got["missing"] == sum(1 for b in got["blocks"]
                                 if b["id"] and b["from"] != "target")


def test_preview_never_reports_the_marker_branch(base, tmp_path, monkeypatch):
    """`fallback` is hardcoded true here — divergence (11) — so `marker` cannot occur.

    Asserted rather than assumed, because the contract says so and a client that
    drew the marker string as prose would be showing an HTML comment to a reader.
    """
    src, _root = _project(base, tmp_path, monkeypatch)
    code, body = _get(base, f"/api/preview?src={src}&lang=zh-TW")
    assert code == 200
    assert all(b["from"] != "marker" for b in json.loads(body)["blocks"])


def test_the_block_map_matches_what_the_cli_answers(base, tmp_path, monkeypatch):
    """Invariant 8, on the wire: one seam, so the two surfaces cannot disagree."""
    from scriptorium.cli import do_blocks

    src, _root = _project(base, tmp_path, monkeypatch)
    code, body = _get(base, f"/api/preview?src={src}&lang=zh-TW")
    assert code == 200
    blocks, missing = do_blocks(src, "zh-TW", load_config(), fallback=True)
    got = json.loads(body)
    assert got["blocks"] == blocks and got["missing"] == missing


def test_sentences_answers_by_index(base, tmp_path, monkeypatch):
    _project(base, tmp_path, monkeypatch)
    code, body = _post(base, "/api/sentences",
                       {"texts": ["He left. She stayed.", "", "他走了。她留下。"]})
    assert code == 200
    assert json.loads(body) == {"sentences": [
        ["He left. ", "She stayed."], [], ["他走了。", "她留下。"]]}


def test_sentences_concatenate_back_to_what_was_sent(base, tmp_path, monkeypatch):
    """The promise a client walks a string with, asserted over the wire itself."""
    _project(base, tmp_path, monkeypatch)
    texts = ["「不要走。」他轉身離開。", "A ⟦1⟧⟦2⟧ run. And more.", "   ", ""]
    code, body = _post(base, "/api/sentences", {"texts": texts})
    assert code == 200
    for sent, text in zip(json.loads(body)["sentences"], texts):
        assert "".join(sent) == text


def test_an_empty_texts_array_is_legal(base, tmp_path, monkeypatch):
    _project(base, tmp_path, monkeypatch)
    code, body = _post(base, "/api/sentences", {"texts": []})
    assert code == 200
    assert json.loads(body) == {"sentences": []}


@pytest.mark.parametrize("payload", [{}, {"texts": None}, {"texts": "a string"},
                                     {"texts": {"a": 1}}, {"texts": 3}])
def test_a_texts_that_is_not_an_array_is_a_400_naming_the_field(
        base, tmp_path, monkeypatch, payload):
    _project(base, tmp_path, monkeypatch)
    code, body = _try_post(base, "/api/sentences", payload)
    assert code == 400
    assert "texts" in json.loads(body)["error"]


def test_an_element_that_is_not_a_string_is_a_400_naming_its_index(
        base, tmp_path, monkeypatch):
    _project(base, tmp_path, monkeypatch)
    code, body = _try_post(base, "/api/sentences", {"texts": ["fine", 7, "fine"]})
    assert code == 400
    assert "texts[1]" in json.loads(body)["error"]


def test_a_refused_sentence_payload_is_never_echoed(base, tmp_path, monkeypatch):
    """A reviewer's editor buffer is what lands here, and it is not repeated back.

    The same rule invariant 6 holds for a credential. Measured with a key-shaped
    string, because that is the paste this protects against.
    """
    _project(base, tmp_path, monkeypatch)
    secret = "sk-live-0123456789abcdef"
    code, body = _try_post(base, "/api/sentences", {"texts": [{"k": secret}]})
    assert code == 400
    assert secret not in body.decode("utf-8") and "sk-live" not in body.decode("utf-8")


def test_sentences_reads_neither_src_nor_lang(base, tmp_path, monkeypatch):
    """It carries no path of any name, so confinement is satisfied by carrying nothing.

    **What the gate at the top of `_post` does to a `src` sent anyway, exactly.**
    This docstring said "is still refused" until 2026-08-21, while the assertion
    below it said `200` — and both the server comment and the contract said the
    same wrong thing. The gate binds by the *presence* of the field rather than
    by the endpoint's name, so it runs; what it does is confine, and a path that
    is already inside the project is not refused by confinement. It is then
    ignored, because this endpoint opens no document.

    The escaping case is the other half and is asserted with it, or "the gate
    still binds" is a claim with nothing behind it.
    """
    _project(base, tmp_path, monkeypatch)
    code, body = _post(base, "/api/sentences",
                       {"texts": ["He left. She stayed."],
                        "src": "no-such-file.md", "lang": "zh-TW"})
    assert code == 200
    assert json.loads(body) == {"sentences": [["He left. ", "She stayed."]]}

    for payload in ({"texts": ["a"], "src": "../../pwn.md"},
                    {"texts": ["a"], "lang": "../../pwn"},
                    {"texts": ["a"], "lang": None}):
        code, _body = _try_post(base, "/api/sentences", payload)
        assert code == 403, payload


# ── asking a backend what it serves ────────────────────────────────────────
#
# `GET /api/models` is the only GET on this surface that leaves the machine —
# `POST /api/translate` has always done so, with more — so what is tested here is
# the shell around that: that a failure is an
# answer rather than a refusal, that the answer still carries what the control
# needs in order to degrade, and that nothing a backend or a hand-edited file
# supplies reaches the browser unmasked. The listing itself is `cli.do_models`
# and is tested in `test_provider.py`.

MODEL_ROWS = {"payload": None}


class _ModelsBackend(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps(MODEL_ROWS["payload"]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def backend():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ModelsBackend)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def _routed(root, backend_url, routing=None, **extra):
    """A project whose `draft` stage names `live`, with the other backends beside it.

    `dead` is port 9 — discard, nothing binds it — with `retries: 0`, so the
    refusal is one attempt and immediate. No test in this file may wait on a
    timeout.
    """
    providers = {
        "live": {"kind": "openai", "base_url": backend_url, "model": "live-model",
                 "api_key_env": "", "timeout": 2, "retries": 0},
        "dead": {"kind": "openai", "base_url": "http://127.0.0.1:9/v1",
                 "model": "dead-model", "api_key_env": "", "timeout": 1, "retries": 0},
    }
    providers.update(extra)
    (root / "lx.config.json").write_text(
        json.dumps({"providers": providers, "routing": routing or {"draft": "live"}}),
        encoding="utf-8")


def _models(base, query=""):
    code, body = _get(base, "/api/models" + query)
    assert code == 200, (code, body)
    return json.loads(body)


def test_a_listing_reaches_the_wire_sorted_with_the_configured_model_beside_it(
        base, tmp_path, monkeypatch, backend):
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend)
    MODEL_ROWS["payload"] = {"data": [{"id": "zeta"},
                                      {"id": "alpha", "status": {"value": "sleeping"}}]}
    assert _models(base) == {
        "provider": "live", "configured": "live-model", "error": None,
        "models": [{"id": "alpha", "status": "sleeping"}, {"id": "zeta", "status": ""}]}


def test_a_backend_that_cannot_be_reached_is_an_answer_and_not_a_refusal(
        base, tmp_path, monkeypatch, backend):
    """The whole reason this endpoint answers 200.

    The control it feeds degrades to a free-text field carrying *the model the
    configuration resolved*, so `configured` has to survive the failure. A 400
    carries a sentence and nothing else, and the page would then have to resolve
    routing a second time to recover it.
    """
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend)
    d = _models(base, "?provider=dead")
    assert d["provider"] == "dead"
    assert d["configured"] == "dead-model", "the degradation path lost `configured`"
    assert d["models"] == []
    assert d["error"] and "dead" in d["error"]


def test_the_key_set_is_the_same_whether_the_listing_worked_or_not(
        base, tmp_path, monkeypatch, backend):
    """`error` is `null` on success rather than absent, and this is why.

    A key present only on failure makes `tests/test_contract.py`'s exact-key
    comparison depend on whether a backend happened to answer — which, under the
    merged default, means whether anything is listening on `localhost:11434`.
    """
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend)
    MODEL_ROWS["payload"] = {"data": [{"id": "m"}]}
    assert set(_models(base)) == set(_models(base, "?provider=dead"))


def test_a_provider_override_reaches_another_backend_and_drops_the_entry_model(
        base, tmp_path, monkeypatch, backend):
    """A model id belongs to the backend that serves it — `config.resolve_route`.

    This is the answer the page is forbidden to compute for itself, and the
    reason a failed listing still carries `configured`: it is neither
    `routing.draft.model` nor any single field `/api/state` holds.
    """
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend, routing={"draft": {"provider": "live", "model": "pinned"}})
    MODEL_ROWS["payload"] = {"data": [{"id": "m"}]}
    assert _models(base)["configured"] == "pinned"
    assert _models(base, "?provider=dead")["configured"] == "dead-model"


def test_an_empty_provider_parameter_means_the_routed_backend(
        base, tmp_path, monkeypatch, backend):
    """`parse_qs` drops a blank value, so this is the absent case, not an error."""
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend)
    MODEL_ROWS["payload"] = {"data": [{"id": "m"}]}
    assert _models(base, "?provider=")["provider"] == "live"


def test_a_malformed_routing_block_is_reported_rather_than_raised(
        base, tmp_path, monkeypatch):
    root = _config_project(tmp_path, monkeypatch)
    (root / "lx.config.json").write_text(
        json.dumps({"providers": ["not", "a", "block"]}), encoding="utf-8")
    d = _models(base)
    assert d["models"] == [] and d["error"]
    assert d["provider"] == "" and d["configured"] == ""


def test_a_hand_edited_userinfo_base_url_is_never_printed_into_the_browser(
        base, tmp_path, monkeypatch, backend):
    """The leak this endpoint was measured to have, 2026-09-01.

    `http.client.InvalidURL` is neither a `ValueError` nor an `OSError`, so it
    became no `ProviderError`, reached none of the masked messages in
    `Provider._request`, and arrived at the outer handler as a `400` whose
    sentence quoted the netloc it choked on — password included, in a body a
    browser renders.
    """
    secret = "SUPERSECRETPASSWORD"
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend, leaky={
        "kind": "openai", "base_url": f"https://user:{secret}@example.invalid/v1",
        "model": "m", "api_key_env": "", "timeout": 1, "retries": 0})
    code, body = _get(base, "/api/models?provider=leaky")
    assert code == 200
    assert secret not in body.decode("utf-8")
    assert "example.invalid" in json.loads(body)["error"], "masked into uselessness"


def test_a_hostile_model_id_never_reaches_the_page(base, tmp_path, monkeypatch, backend):
    """Dropped at the boundary rather than escaped at the print.

    The last assertion is the one worth keeping: the browser has a category the
    terminal does not, and the boundary filter does **not** cover it. `<` and `>`
    are legal in a model id, so escaping is the page's job and the contract says
    so. Narrowing the filter here would hide that from a rebuild.
    """
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend)
    MODEL_ROWS["payload"] = {"data": [{"id": "good"},
                                      {"id": "evil\x1b[2Kforged"},
                                      {"id": "flip‮esrever"},
                                      {"id": "<img src=x onerror=alert(1)>"}]}
    ids = [m["id"] for m in _models(base)["models"]]
    assert "evil\x1b[2Kforged" not in ids and "flip‮esrever" not in ids
    assert "good" in ids
    assert "<img src=x onerror=alert(1)>" in ids


def test_the_provider_row_carries_the_four_numbers_a_settings_form_prefills(
        base, tmp_path, monkeypatch, backend):
    """Additive on the *provider* shape, and `null` means inherited, not broken.

    A string a `float`/`int` accepts is coerced rather than refused, because
    `Provider.__init__` coerces it too and `"timeout": "300"` translates
    perfectly today — calling that unreadable would be a false accusation on a
    working configuration.
    """
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend, stringy={"kind": "openai", "base_url": "http://x/v1",
                                    "api_key_env": "", "timeout": "300",
                                    "max_tokens": "4096"})
    rows = {p["name"]: p for p in json.loads(_get(base, "/api/state")[1])["providers"]}
    assert rows["live"]["timeout"] == 2 and rows["live"]["retries"] == 0
    assert rows["live"]["temperature"] is None, "an absent key is null, not a default"
    assert rows["live"]["max_tokens"] is None
    assert rows["stringy"]["timeout"] == 300 and rows["stringy"]["max_tokens"] == 4096
    assert "error" not in rows["stringy"], "a working configuration was called unreadable"


def test_a_number_no_coercion_accepts_is_null_and_is_named(
        base, tmp_path, monkeypatch, backend):
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend, broken={"kind": "openai", "base_url": "http://x/v1",
                                   "api_key_env": "", "timeout": "soon"})
    rows = {p["name"]: p for p in json.loads(_get(base, "/api/state")[1])["providers"]}
    assert rows["broken"]["timeout"] is None
    assert "timeout" in rows["broken"]["error"]


def test_an_unbuildable_provider_still_answers_200_with_configured(
        base, tmp_path, monkeypatch, backend):
    """The endpoint's boldest promise, on the shapes that used to break it.

    `providers.build` raised `AttributeError` / `TypeError` / a bare
    `ValueError` for these, none of which `_models` caught — so the endpoint
    answered `400`, lost `configured`, and put a raw Python exception string in
    front of the reader. On a numeric knob that string carried the configured
    value into a browser.
    """
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend, scalar=5, wordy={"kind": "openai", "timeout": "soon"},
            headery={"kind": "openai", "headers": "nope"})
    for name in ("scalar", "wordy", "headery"):
        d = _models(base, "?provider=" + name)
        assert d["provider"] == name, name
        assert d["models"] == [] and d["error"], name


def test_a_refusal_over_the_wire_never_repeats_a_mispasted_knob(
        base, tmp_path, monkeypatch, backend):
    pasted = "sk-REDACTEDLOOKINGVALUE0123456789"
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend, oops={"kind": "openai", "timeout": pasted})
    code, body = _get(base, "/api/models?provider=oops")
    assert code == 200
    assert pasted not in body.decode("utf-8")


def test_a_bad_port_beside_userinfo_does_not_take_the_whole_page_down(
        base, tmp_path, monkeypatch, backend):
    """`/api/state` is the bootstrap endpoint: if it 400s the workbench cannot
    draw at all — including the editor that exists to repair this very file."""
    secret = "SUPERSECRETPASSWORD"
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend, badport={
        "kind": "openai", "base_url": f"https://a:{secret}@example.invalid:notaport/v1",
        "api_key_env": "", "timeout": 1, "retries": 0})
    for path in ("/api/state", "/api/models?provider=badport"):
        code, body = _get(base, path)
        assert code == 200, (path, body)
        assert secret not in body.decode("utf-8"), path


def test_a_non_finite_knob_leaves_the_state_endpoint_parseable(
        base, tmp_path, monkeypatch, backend):
    root = _config_project(tmp_path, monkeypatch)
    _routed(root, backend, inf={"kind": "openai", "base_url": "http://x/v1",
                                "api_key_env": "", "timeout": "Infinity"})
    code, body = _get(base, "/api/state")
    assert code == 200
    text = body.decode("utf-8")
    assert "Infinity" not in text and "NaN" not in text, "the body is not JSON"
    json.loads(text)
