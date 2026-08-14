"""The workbench is a shell over the CLI, so these tests only prove the shell holds."""

import http.client
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import scriptorium.cli as cli  # noqa: E402
import statedb  # noqa: E402
from scriptorium.cli import UnsafePath, confined_path  # noqa: E402
from scriptorium.store import target_token  # noqa: E402
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
