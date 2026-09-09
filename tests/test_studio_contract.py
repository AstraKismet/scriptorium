"""The frontend's type definition file is compared against the frozen contract.

`studio/web/src/contract.ts` is the shared type definition `AGENTS.md` invariant 8
calls for — plain JSON over HTTP, one type file, no typed RPC framework. It is a
hand translation of `docs/contracts/workbench-http.md`, and a hand translation
drifts unless something compares it.

Three links chain, and this file is the middle one:

1. Inside `contract.ts`, a block of type-level assertions fails `tsc --noEmit` if
   `RESPONSE_KEYS` and the interfaces stop agreeing, in either direction. That
   guard needs Node.
2. **Here**: `RESPONSE_KEYS` against the contract document's own `**Response**`
   tables, endpoint by endpoint, in both directions. This runs in the Python
   suite, which is what CI runs, so the comparison does not depend on anybody
   remembering to run `npm`.
3. `tests/test_contract.py` compares the document against a live reply.

So a key renamed on the wire moves the server, which moves (3); a key renamed in
the document moves (2); a key renamed in the type file moves (2) and (1). There
is no edit that leaves all three green and the four surfaces disagreeing.

The extraction here deliberately reuses `test_contract.py`'s own regex for the
document side rather than writing a second one — two readers of one table is how
they come to disagree about what a row is.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium.web.server import CONTRACT_VERSION  # noqa: E402
from test_contract import _documented, _documented_response_keys, _read  # noqa: E402

_ROOT = os.path.join(os.path.dirname(__file__), "..")
STUDIO = os.path.join(_ROOT, "studio", "web")
TYPES = os.path.join(STUDIO, "src", "contract.ts")
STATIC = os.path.join(_ROOT, "src", "scriptorium", "web", "static")


def _sources():
    """Every TypeScript file the frontend is written in, as `(name, text)`.

    `node_modules` is excluded by walking `src/` only — nothing this project
    wrote lives anywhere else under `studio/web/`, and a scan that reached a
    dependency tree would be reading a hundred megabytes to answer a question
    about four thousand lines.
    """
    root = os.path.join(STUDIO, "src")
    out = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != "node_modules"]
        for name in sorted(names):
            if name.endswith((".ts", ".tsx")):
                path = os.path.join(base, name)
                out.append((os.path.relpath(path, STUDIO).replace("\\", "/"), _read(path)))
    return out


def _declared_response_keys():
    """`{(method, path): {key, …}}` as `contract.ts`'s `RESPONSE_KEYS` states it.

    Read with a regex rather than by running TypeScript, for the reason
    `AGENTS.md` gives for every guard in this repository: the weakest thing that
    decides the question. If the object is ever restructured this finds nothing
    and the floor assertion below fails, which is the intended outcome — whoever
    restructures it owns teaching this file how to read it.
    """
    text = _read(TYPES)
    block = re.search(
        r"^export const RESPONSE_KEYS = \{$(.*?)^\} as const", text, re.M | re.S
    )
    assert block, "RESPONSE_KEYS is not declared the way this test reads it"
    out = {}
    for line in block.group(1).splitlines():
        row = re.match(r"^\s*'(GET|POST) (/api/\S+)':\s*\[(.*)\],\s*$", line)
        if not row:
            continue
        keys = set(re.findall(r"'(\w+)'", row.group(3)))
        assert keys, f"{row.group(1)} {row.group(2)} declares no response keys"
        out[(row.group(1), row.group(2))] = keys
    return out


def test_the_type_file_exists_and_declares_a_row_per_endpoint():
    """The floor. Everything below compares sets; this proves there was a set."""
    declared = _declared_response_keys()
    assert len(declared) >= 17, (
        f"only {len(declared)} endpoints found in RESPONSE_KEYS. Either the "
        f"frontend has stopped covering the surface or this test can no longer "
        f"read the declaration — check which before editing either."
    )


def test_the_type_file_covers_exactly_the_endpoints_the_contract_documents():
    documented = _documented()
    declared = set(_declared_response_keys())
    assert declared - documented == set(), (
        "studio/web/src/contract.ts names endpoints the contract does not: "
        f"{sorted(declared - documented)}"
    )
    assert documented - declared == set(), (
        "the contract documents endpoints the frontend's type file does not "
        f"cover: {sorted(documented - declared)}. A client that has not been "
        f"told about an endpoint cannot call it, and adding one to the wire "
        f"without adding it here is how the two drift."
    )


@pytest.mark.parametrize(
    ("method", "path"), sorted(_documented()), ids=lambda v: str(v)
)
def test_the_type_file_declares_exactly_the_keys_the_contract_documents(method, path):
    """Both directions, per endpoint.

    A missing key is a field the frontend cannot read; an extra one is a field
    it will read as `undefined` forever, which is the worse of the two because
    nothing fails.
    """
    documented = _documented_response_keys()[(method, path)]
    declared = _declared_response_keys()[(method, path)]
    assert declared == documented, (
        f"{method} {path}: contract.ts and the contract document disagree.\n"
        f"  only in contract.ts: {sorted(declared - documented)}\n"
        f"  only in the document: {sorted(documented - declared)}"
    )


def test_the_frontend_pins_the_contract_version_the_server_reports():
    """The client refuses a `contract_version` it does not know, so the number it
    knows has to be this build's.

    A bump is a work package, and this is the line in it that says the frontend
    was part of the decision rather than discovering it at runtime.
    """
    text = _read(TYPES)
    found = re.search(r"^export const CONTRACT_VERSION = (\d+)$", text, re.M)
    assert found, "contract.ts does not declare CONTRACT_VERSION where this test reads it"
    assert int(found.group(1)) == CONTRACT_VERSION, (
        f"the frontend pins contract_version {found.group(1)} and the server "
        f"reports {CONTRACT_VERSION}. The client refuses a number it does not "
        f"know, so shipping this pair is shipping a workbench that will not start."
    )


# `reset` and `tone` are the two fields `POST /api/extract` type-checks with
# nothing — *Known divergences* (28), open. `reset` is read for truthiness in
# Python, so the string `"false"` is a reset that discards a document's
# translations, and a truthy non-string `tone` is frozen onto the document
# verbatim. There is no server-side guard; the client is the guard.
#
# The predecessor of this test read `web/static/index.html` and asserted the page
# never mentioned either word. That test could not survive the rebuild in either
# direction: the file it read is now a build artifact whose script is bundled
# elsewhere, so the scan would have passed **vacuously**; and the requirement
# itself moved, because a register control has to send both keys.
#
# So the rule is not "never" any more, it is "once": both spellings may appear at
# a call site in exactly one file, and a second one is a second chance to get the
# type wrong.
_RESET_OR_TONE = re.compile(
    r"""(?:\breset\s*:|["']reset["']\s*:|\.\s*reset\s*=[^=])"""
    r"""|(?:\btone\s*:|["']tone["']\s*:|\.\s*tone\s*=[^=])"""
)

#: `contract.ts` declares the two fields on `ExtractRequest`; `store.ts` holds the
#: single call site, `reExtract`. `store.test.ts` is the executed half of this
#: guard — it asserts that what goes on the wire is a JSON boolean and a register
#: a person chose — and it cannot do that without naming them.
#:
#: `App.test.tsx` is here for a different and weaker reason, said out loud rather
#: than hidden in the set: it holds a `GET /api/doc` fixture, and `tone` is a key
#: of that **reply**. The scan reads text and cannot tell a request from a
#: response, so a fixture describing what the server sends trips a guard about
#: what the client sends. The list is the price of a guard that needs no parser,
#: and the property it protects is unchanged — a module that is not on it and
#: names either key is a new place a register can be sent from, and somebody has
#: to look at it.
_MAY_NAME_RESET = {
    "src/contract.ts",
    "src/store.ts",
    "src/store.test.ts",
    "src/App.test.tsx",
}


def test_only_one_place_in_the_frontend_names_reset_or_tone_on_a_request():
    named = {name for name, text in _sources() if _RESET_OR_TONE.search(text)}
    assert named <= _MAY_NAME_RESET, (
        f"{sorted(named - _MAY_NAME_RESET)} name `reset` or `tone` as an object "
        f"key. POST /api/extract type-checks neither — the string \"false\" is a "
        f"reset that destroys a document's translations — so every request that "
        f"carries them is written in one function, store.ts's `reExtract`, and a "
        f"second call site is a second chance to send a string. If a new control "
        f"needs to start a document over, call `startOver(tone)`."
    )


def test_the_one_place_that_names_them_sends_a_boolean_and_a_chosen_register():
    """The other half: the guard above would pass if nothing sent them at all."""
    store = _read(os.path.join(STUDIO, "src", "store.ts"))
    assert "reset: true" in store, (
        "store.ts no longer sends `reset: true` as a JSON boolean literal. "
        "Anything else — a variable, a string, a number — is a value the server "
        "does not check and Python reads for truthiness."
    )
    assert re.search(r"\{\s*\.\.\.where,\s*reset: true,\s*tone\s*\}", store), (
        "`reset` and `tone` are no longer sent together in one object literal. "
        "The endpoint answers 400 to a reset that names no register, and the "
        "register a client guesses is not refused — it is accepted and wrong."
    )


def test_the_frontend_never_builds_markup_from_a_string():
    """Divergence (21)'s lesson, carried forward.

    The server sends no `Content-Security-Policy` header — its header set is
    itself part of the frozen contract — and the page it serves reaches every
    endpoint on this surface unauthenticated, including ones that spend money.
    JSX escapes by construction; `dangerouslySetInnerHTML` hands that property
    straight back, and the defect it caused last time was found in an attribute
    an audit's own list had missed.
    """
    # The prop, not the word: this file's own neighbours explain in prose why
    # they do not use it, and a scan that could not tell the two apart would make
    # the explanation unwritable.
    used = re.compile(r"dangerouslySetInnerHTML\s*[=:]")
    named = [name for name, text in _sources() if used.search(text)]
    assert not named, (
        f"{named} build markup from a string. Every value on this surface is "
        f"either remote text (a model id), a filename, or a reviewer's own prose."
    )


def test_the_built_workbench_is_committed_and_the_page_asks_for_it():
    """`lx web` must work from a bare checkout with no Node installed.

    That is invariant 1's four protected situations — a bare interpreter, CI, an
    agent sandbox and a locked-down machine — and the arrangement that satisfies
    it is: source in `studio/web/`, build output committed here. A checkout whose
    `static/` holds a page referring to a script that is not beside it is a blank
    workbench, and it looks fine in the dev server.
    """
    page = os.path.join(STATIC, "index.html")
    assert os.path.exists(page), "src/scriptorium/web/static/index.html is missing"
    text = _read(page)
    asked = set(re.findall(r'src="/([^"]+\.js)"', text))
    assert asked, (
        "the committed index.html asks for no script. Run `npm run build` in "
        "studio/web/ and commit what it writes."
    )
    for name in sorted(asked):
        beside = os.path.join(STATIC, name)
        assert os.path.exists(beside), (
            f"index.html asks for /{name} and it is not in static/. The build "
            f"output is committed on purpose; run `npm run build` in studio/web/."
        )
        assert os.path.getsize(beside) > 1024, f"/{name} is committed but empty"


def test_the_build_output_is_flat():
    """No subdirectory under `static/`, and the reason is a measured defect.

    `pyproject.toml`'s package-data glob was single-level until 2026-08-14, so a
    hashed `assets/` subdirectory shipped a wheel whose workbench was a blank
    page while the checkout and the dev server both looked fine. The glob is
    recursive now and would ship it — this stays because the transport makes a
    hashed filename worthless anyway (`Cache-Control: no-store`, no `ETag`, one
    connection per file on HTTP/1.0), so more files buy nothing but more
    connections.
    """
    nested = [
        name for name in os.listdir(STATIC)
        if os.path.isdir(os.path.join(STATIC, name))
    ]
    assert not nested, (
        f"the build wrote subdirectories into static/: {nested}. Keep the output "
        f"flat — see vite.config.ts, which sets assetsDir and fixed filenames."
    )
