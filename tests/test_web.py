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

import statedb  # noqa: E402
from scriptorium.cli import UnsafePath, confined_path  # noqa: E402
from scriptorium.web import server as web_server  # noqa: E402
from scriptorium.web.server import _Handler, _own_hosts, _own_origins  # noqa: E402


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
