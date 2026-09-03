"""Command line interface. Every other surface — the skill, the web UI, CI — is
a caller of these functions, so behaviour cannot diverge between them."""

import argparse
import glob
import json
import math
import os
import re
import sys
import urllib.parse
from collections import Counter

from . import __version__, formats, sentences
from .checks import HELD, check_segment, is_held, is_waived, workable
from .config import (
    DEFAULT_TONE,
    GLOSSARY_HEADER,
    MISSING,
    PATH_VALUED_KEYS,
    ROUTING_STAGES,
    STATE,
    ConfigError,
    StyleSheetError,
    canonical_tone,
    dump_json,
    get_in,
    has_version_segment,
    load_config,
    load_dnt,
    load_glossary,
    load_json,
    load_style,
    printable_url,
    resolve_route,
    route_entry,
    set_in,
    split_key,
    unset_in,
    write_templates,
)
from .docio import (
    UndecodableDocument,
    apply_terminator,
    apply_terminator_parts,
    read_document,
    split_terminator,
    write_document,
    write_document_to_stdout,
)
from .formats import UnknownFormat
from .mask import placeholder_ids, repair_placeholders
from .normalize import normalize, polish_rendered, reseat_outer_blanks

# `main`'s `except` tuple needs this name at module scope. It comes from
# `providers.errors`, which imports nothing, rather than from `providers` —
# importing the package pulls `urllib.request` and with it `ssl`, `http.client`,
# `socket` and fifteen `email` submodules into every command. Measured
# 2026-08-20: that roughly doubles the cost of importing `scriptorium.cli`,
# 41 ms to 77 ms, which `lx --help` on a bare interpreter should not pay. The
# rest of `providers` stays behind a function-local import, with `translate`.
from .providers.errors import ProviderError
from .store import (
    HUMAN,
    StateVersionError,
    append_tm,
    db_path,
    doc_id,
    doc_label,
    is_regenerable_origin,
    load_doc,
    load_tm,
    no_carryover,
    prior_doc,
    prior_targets,
    report_path,
    save_doc,
    save_issues,
    save_review,
    save_segments,
    save_targets,
    save_waived,
    target_token,
    tm_lookup,
    tm_records,
    tracked,
)


def _out(msg):
    print(msg, flush=True)


def force_utf8(stream):
    """Make a diagnostic stream carry UTF-8 whatever the platform picked for it.

    On Windows an interactive console gets UTF-16 through `WriteConsoleW` and is
    fine; redirect it or pipe it and the stream falls back to the locale code
    page. Three commands emit non-ASCII — `todo` prints a literal ⟦n⟧ in its
    rules line, `check` prints validator messages containing CJK, and `render
    --out -` writes a whole translated document — so
    `lx todo doc.md --lang zh-TW > todo.json` died with UnicodeEncodeError. CI
    never saw it because the only redirect in the workflow runs on Ubuntu.

    This is for *diagnostics*. `render --out -` is a document, not a message, and
    goes out through `sys.stdout.buffer` in `docio`, which no text layer touches
    — deliberately two repairs, because that one is also a newline problem and
    this one is not.

    Guarded rather than assumed: `sys.stdout` is replaced by a stand-in under
    test and in embedded callers, and not every stand-in is a `TextIOWrapper`.
    Doing nothing is the right answer there — a stand-in has already decided how
    it encodes.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):  # detached, closed, or already written through
        pass


# ── path confinement ───────────────────────────────────────────────────────

#: Windows resolves these from any directory, so a path that lands INSIDE the
#: root can still name a device. `ntpath.isreserved` would do this and arrived
#: in 3.13; the floor is 3.9, so it is a table.
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    # The console pair 3.13's `ntpath.isreserved` also misses. Measured:
    # {"out": "CONOUT$"} answered 200 and the rendered document went to the
    # server's console and nowhere else — the exact silent discard this table
    # exists to stop — while CONIN$ 400ed with a console-input error.
    + ["CONIN$", "CONOUT$"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)

#: A language tag is letters, digits, `-` and `_`, first character alphanumeric.
#: BCP 47 allows no more; 35 characters covers `sr-Latn-RS-x-private` and then
#: some.
_LANG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,34}\Z")


class UnsafePath(ValueError):
    """A caller-supplied path this project will not open. The message says why."""


class UnwritableKey(ValueError):
    """A configuration key an untrusted caller may not write. See `writable_key`.

    Its own class rather than a second meaning for `UnsafePath`, which the
    surface beside it turns into the same `403`. Three reasons, and the first is
    the one that matters: `UnsafePath` says "a caller-supplied *path* this
    project will not open", and a configuration key is not a path. A refusal
    wearing a name that describes something else is the axis a security-tier
    pass caught this project's own contract draft on — `docs/decisions.md`,
    2026-08-13, and the ledger row in `docs/conventions/delegated-work.md` §7.

    The other two are mechanical. `UnsafePath`'s message convention is
    `{field} = {value!r}`, which *repeats* what it refused — right for a path a
    person typed, wrong for a field that may hold a credential. And
    `tests/test_contract.py` keys its parametrized confinement assertions on the
    `"src ="` sentinel that convention produces, so a second meaning would make
    a passing assertion ambiguous about which control had fired.
    """


class UnsupportedSource(ValueError):
    """A command that only works on one kind of source was given another.

    Raised rather than answered, and caught in :func:`main` for exit 2, because
    the alternative for `lx terms` on a non-English document is a list of quiet
    nonsense — Chinese and Japanese have no capitalization for the rule to read,
    so it would propose nothing and report success.
    """


class UnusableTarget(ValueError):
    """A save payload this build will not store, and why.

    One class for three refusals — a target that is empty or all blanks, a target
    that is not text at all, and a `base` that is not a map of tokens — because
    they share what the caller does about them: fix the payload and send it again.

    Named rather than a bare `ValueError` so `main` answers each with one sentence
    and exit 2, the way every other refusal in this CLI does, instead of a
    traceback — and so `web/server.py` turns each into the 400 the contract states
    rather than into whatever exception happened to escape first. The measured one
    was `AttributeError: 'int' object has no attribute 'strip'`, from a numeric
    target reaching the blank check: exit 1 and a stack trace on the CLI, and a
    400 quoting a CPython internal on the wire.
    """


class UnnamedRegister(ValueError):
    """A `--reset` that does not say which register to refreeze.

    Its own class rather than a fourth meaning for `UnusableTarget`, whose
    docstring earns being one class by covering three refusals of a *save
    payload* that share one remedy. A missing register is not a payload and the
    remedy is to supply a value the request never carried, so folding it in would
    generalize that docstring into "a request this build will not accept" and
    widen five existing `pytest.raises(UnusableTarget)` assertions onto an
    unrelated failure.

    Why it is refused rather than guessed: `--reset` reads no prior state — that
    is what it is for, since the state row may be one this build refuses to read
    — so it cannot keep the register the document was frozen in. Until
    2026-08-19 it silently refroze to the configured default, and because the
    register is a field of the translation-memory key, a `literary` novel
    re-extracted through a "start over" button came back `technical` and the next
    `lx commit` banked the whole book under the wrong key. Measured 2026-08-13,
    with nothing printed: `cmd_extract` names the register only when it differs
    from the default, so the CLI's own output stayed silent about the loss.
    """


class GlossaryWriteError(OSError):
    """The glossary could not be replaced, and nothing in it was changed."""


def confined_path(value, field="path", root=None):
    """Refuse a path that is not a file inside `root`; return the caller's own string.

    It validates and hands back `value` byte for byte. It deliberately does
    **not** canonicalize. *Lost:* returning `os.path.realpath(value)`, which was
    measured to split one document into two rows of state under a junction or an
    8.3-short-name cwd — the GitHub Actions windows-latest layout — and to make
    `/api/render` write into a directory nobody asked for, with no exception to
    point at. Every identity in this project is `os.path.relpath(src)` against
    `os.getcwd()`: `store.doc_id`, `store.report_path`,
    `do_extract`'s `doc["source"]` and `default_output` all read it. Hand any of
    them a resolved path and the same document quietly acquires a second name.

    The root is `os.path.realpath(os.getcwd())`, recomputed per call and never
    captured at import. The module is imported once for a whole session, the
    workbench is started inside a project, and the tests move the cwd between
    requests in one process — a module-level constant would answer for whichever
    directory Python happened to start in.

    Both sides are resolved before they are compared, because creating a junction
    or a symlink needs no elevation: anyone who could write inside the project
    would otherwise own the confinement. `os.path.commonpath` rather than
    `str.startswith` — *lost* because `startswith` matches `/proj-evil` against
    `/proj`, is case-sensitive where Windows is not, and rejects every child when
    the root is a drive root. *Also lost:* `pathlib.Path.is_relative_to`, which
    3.9 does have, but which is the same string comparison with the same
    blindness to `..`.

    Six mechanical rules run in front of the resolution — non-text or empty, a
    NUL, a drive-relative spelling, an alternate data stream, a trailing dot or
    space, a reserved device name — because resolution cannot see any of them.
    Each resolves *inside* the root and still names something other than the file
    the caller wrote down.

    They are unconditional rather than gated on `os.name`, and that costs
    something real: on Linux a legal filename containing `:`, ending in `.` or a
    space, or whose stem is `nul`, `con`, `aux`, `prn`, `com1`–`com9` or
    `lpt1`–`lpt9` cannot be reached through the workbench. It is paid on purpose
    — one rule means one behaviour and one test on all four runners, and a
    project laid out on Linux stays openable on Windows. `lx` from a terminal
    still reaches such a file, because the CLI does not call this.

    TOCTOU is not closed: a component can be swapped between this check and the
    open. It is not mitigated either, and that is the honest answer rather than a
    gap — winning that race needs write access inside the project root, which
    needs a local process running as this user, and such a process can already
    write anything this user can. The browser page this defends against has no
    filesystem access at all.
    """
    if root is None:
        # Per call, never captured. `serve()` is not called by the tests, the
        # module is imported once for the whole session, and several tests
        # monkeypatch.chdir while the server runs — so the correct root really
        # does move between requests inside one process.
        root = os.path.realpath(os.getcwd())

    if not isinstance(value, str) or not value.strip():
        raise UnsafePath(
            f"{field} must be the path of a file inside the project directory, as text "
            f"— got {value!r}. Try {field}=\"docs/guide.md\".")
    if "\x00" in value:
        # Refused here rather than left to the platform: POSIX realpath raises
        # ValueError out of os.lstat, Windows passes the NUL through normpath,
        # realpath and commonpath and only open() objects — and 3.9 and 3.12
        # differ again on Windows. One rule, one behaviour, four CI runners.
        raise UnsafePath(
            f"{field} contains a NUL byte, which no filesystem accepts. Send the path "
            f"as plain text, e.g. {field}=\"docs/guide.md\".")

    # A backslash is a separator on every platform, exactly as `_static` already
    # decides. `docs\..\..\out.txt` climbs out on Windows and is one literal
    # filename on Linux; rewriting here means the rule — and the test that pins
    # it — is the same on both runners. Only the PROBE is rewritten; `value` is
    # returned untouched, because rewriting what the caller opens is the silent
    # rewrite this package forbids.
    probe = value.replace("\\", "/")

    # os.path.splitdrive, not ntpath.splitdrive. On Linux posixpath returns no
    # drive, so a `C:` component stays a component and is refused by the colon
    # rule below — which makes `C:/Windows/win.ini` a 403 on BOTH runners.
    drive, rest = os.path.splitdrive(probe)
    if drive and not rest:
        # `ntpath.splitdrive` maps `//./NUL`, `//server/share` and a bare `C:`
        # all to (drive, "") — a root, with no file after it. Measured before
        # this branch existed: `//./NUL` fell into the drive-relative refusal
        # below, whose advice — "write it out in full, e.g. //./NUL/docs/..."
        # — cannot be followed. `//server/share/x` has a non-empty rest and
        # must keep falling through to the containment refusal instead.
        raise UnsafePath(
            f"{field} = {value!r} names a drive, device or share root, not a file in the "
            f"project. Give a path relative to the project directory, e.g. "
            f"{field}=\"docs/guide.md\".")
    if drive and not rest.startswith("/"):
        # Drive-relative. Measured: ntpath.join("C:\\proj", "C:foo") is
        # "C:\\proj\\foo" when the root is on C:, so the check would ALLOW it
        # while silently reinterpreting the caller's meaning; with the root on
        # another drive the same string raises in commonpath. One spelling, two
        # answers, one of them a silent rewrite. Refuse it in both.
        raise UnsafePath(
            f"{field} = {value!r} is relative to another drive's current directory, which "
            f"names a different file in every process. Write it out in full, e.g. "
            f"{drive}/docs/guide.md.")

    for part in rest.split("/"):
        # "." and ".." are exempt on purpose: they are decided by resolution,
        # not by spelling, and the trailing-dot rule below would otherwise
        # refuse `docs/../docs/guide.md`, which must be allowed.
        if part in ("", ".", ".."):
            continue
        if ":" in part:
            # NTFS alternate data stream. Measured: writing `docs/g.md:evil`
            # succeeded and left g.md's size and directory listing unchanged — a
            # covert write onto a file the pipeline treats as input. Invisible to
            # realpath+commonpath, because it resolves inside the root.
            raise UnsafePath(
                f"{field} = {value!r} has a ':' in {part!r}. On Windows that names an "
                f"alternate data stream — bytes written into a file that appears "
                f"unchanged. Rename it, or reach it with `lx` from a terminal.")
        if part[-1] in ". ":
            # Windows strips a trailing dot or space, so `out.md.` and `out.md`
            # are one file on disk and two different `doc_id`s. It also catches
            # the dot-run components `.. `, `...`, `....`, whose Windows
            # resolution is surprising enough that no rule should depend on it.
            raise UnsafePath(
                f"{field} = {value!r} ends {part!r} with {part[-1]!r}. Windows drops it, so "
                f"the name you sent and the file on disk would differ. Send the name "
                f"without it.")
        if part.split(".")[0].upper() in _RESERVED_NAMES:
            # Measured: open(root/NUL, "wb") succeeded, os.listdir did not contain
            # it, and POST /api/render {"out": "NUL"} answered 200 with
            # {"wrote": "NUL"} while the translated document was discarded. CON
            # and COM1 behaved as ordinary files on that build — which is the
            # point: the behaviour is build-dependent, and invariant 4 wants the
            # decidable rule rather than the observed one.
            raise UnsafePath(
                f"{field} = {value!r} names the reserved device "
                f"{part.split('.')[0]!r}. Writing to it discards the bytes and reading "
                f"it can hang. Choose another name.")

    try:
        # realpath BEFORE commonpath, and both sides resolved. commonpath is a
        # string operation: commonpath(["C:\\proj", "C:\\proj\\..\\etc"]) returns
        # "C:\\proj" and would ALLOW, and an unresolved join through a junction
        # satisfies startswith(root + os.sep) equally. Non-strict realpath,
        # because `out` legitimately does not exist yet — and because posixpath
        # only learned strict= in 3.10, so passing it is a TypeError on 3.9.
        full = os.path.realpath(os.path.join(root, probe))
        # Root first: ntpath.commonpath lowercases before comparing but returns
        # the casing of its FIRST argument, so the other order turns a case
        # difference in the candidate into a false rejection.
        inside = os.path.commonpath([root, full]) == root
    except (TypeError, ValueError, OSError) as e:
        # Converted, never propagated. commonpath raises ValueError on a Windows
        # drive mismatch — another drive, a UNC path, a \\?\ path — and TypeError
        # on a non-path. Letting either reach the handler's bare except gives a
        # 400 quoting a CPython internal, which hides which control fired.
        raise UnsafePath(
            f"{field} = {value!r} is not a path under the project directory {root} ({e}). "
            f"Use a path relative to it, e.g. {field}=\"docs/guide.md\".") from None
    if not inside:
        raise UnsafePath(
            f"{field} = {value!r} is outside the project directory {root}. The workbench "
            f"opens files only under the directory it was started in — restart it in the "
            f"project you mean, or use `lx` from a terminal.")
    if full == root:
        # "" and "." are the shapes an empty field produces, and both resolve
        # to the root — which containment correctly ALLOWS, or the root's own
        # children would fail. Refusing the root here turns that one case into
        # a sentence naming the field and the fix. Any OTHER directory still
        # fails later, at open(), with a platform-specific message; an isdir()
        # check here was not taken, because it would put a second filesystem
        # probe in a helper that is otherwise one resolution.
        raise UnsafePath(
            f"{field} = {value!r} names the project directory itself, not a file in it. "
            f"Give a file, e.g. {field}=\"docs/guide.md\".")
    return value


def language_tag(value, field="lang"):
    """Refuse anything that is not a language tag, because `lang` becomes a filename.

    A whitelist, not a confinement. `lang` is interpolated straight into a file
    *name* by `store.report_path` and `store.tm_path`, and it reaches both from a
    request body. Measured on this repository, 2026-07-29:
    `tm_path("../../../../pwn")` lands one directory above the project, and the
    document state file of the day landed beside it in the project root — so
    closing `src` and `out` while leaving `lang` open would have shipped the same
    write primitive under a different name. Document state is a database row since
    2026-08-02 and `lang` is a column value there, which narrows what this
    protects but does not remove it: a check that loosens whenever storage moves
    is a check nobody can rely on.

    *Lost:* confining the paths derived from it. That would still allow a tag to
    escape `.lx/` into the project and collide with a source document, and the
    answer would depend on which of the three paths an endpoint happens to build.
    A language tag has a decidable shape (invariant 4), so the whitelist refuses
    every separator by construction rather than by resolution.
    """
    if not isinstance(value, str) or not _LANG_RE.match(value):
        raise UnsafePath(
            f"{field} = {value!r} is not a language tag. It becomes part of a filename in "
            f".lx/, so it is letters, digits, '-' and '_' only — e.g. {field}=\"zh-TW\".")
    return value


# ── extract ────────────────────────────────────────────────────────────────

def _protected(seg, proposal, dnt):
    """Whether a proposal's placeholders would resolve to a do-not-translate term.

    The transition rule for a memory line that carries no slot map — every line
    banked before 2026-08-17. Such a line cannot say what its ids meant, so the
    only gate it has is the id set, which a wholesale renumbering satisfies. This
    offers it anyway where a renumbering cannot have moved it, and that is
    decidable: `mask.mask` numbers every inline match first and the terms after,
    so a markup slot's id is a pure function of the source text — which the
    content hash has already fixed — while a term's id depends on the list. A hit
    whose placeholders are all markup was never exposed and is reused as it is.

    Measured on this repository at the time: 0.6% of segments carry a
    do-not-translate slot with the shipped list, against 34.8% carrying any slot,
    so refusing the whole placeholder-bearing population instead would discard
    reuse that was never at risk.
    """
    protected = {rec["original"] for rec in (seg.get("slots") or {}).values()
                 if rec.get("original") in set(dnt)}
    if not protected:
        return False
    return any((seg["slots"].get(pid) or {}).get("original") in protected
               for pid in placeholder_ids(proposal))


def do_extract(src, lang, cfg, tone=None, reset=False):
    # The first statement, above even the lazy import: this is decidable from two
    # arguments, so nothing the document or the database could say changes the
    # answer, and a refused request must not import the provider stack, read the
    # user's file or open `.lx/state.db`. It has to sit above the `tone or ...`
    # resolution below as well, which rebinds `tone` to a truthy value and would
    # make a guard placed after it unreachable — the guard-fires-once shape.
    # The cost, accepted: `lx extract missing.md --lang zh-TW --reset` now names
    # the register before it names the missing file, which is the right order,
    # since the missing `--tone` is a defect in the command as typed.
    #
    # Blank rather than `is None`: `canonical_tone` folds None, `""` and `"   "`
    # onto the default register, so `--tone ""` on the CLI or `{"tone": ""}` on
    # the wire would walk around this and land on exactly the silent `technical`
    # it exists to stop. Truthiness on `reset` for the same reason — the endpoint
    # passes `body.get("reset", False)` through unvalidated.
    #
    # No second remedy is offered, and "or drop --reset" is the one that must not
    # be: `store._refuse_if_newer` sends a newer-state user *to* `--reset`, so the
    # two sentences would form a loop with no exit. And the command is one word
    # short of runnable on purpose — a paste-ready `--tone technical` makes
    # reproducing this defect the path of least resistance.
    if reset and not str(tone or "").strip():
        raise UnnamedRegister(
            f"--reset discards the register frozen on {src} [{lang}] along with the "
            f"translations, and it reads no prior state, so it cannot put one back — "
            f"name the register it should be in. `lx extract {src} --lang {lang} "
            f"--reset --tone <technical|literary>`, or `tone` beside `reset` on the "
            f"wire. Nothing was written. The register is part of the "
            f"translation-memory key, so a book refrozen into the wrong one stops "
            f"finding every wording banked in the right one.")
    # Lazy, like every other `.translate` import in this file: extract does not
    # talk to a model and should not pull the provider stack in to do so.
    from .translate import accept

    # The format is chosen from the path here and frozen onto the document below,
    # so every later command reads the skeleton with the parser that wrote it.
    fmt = formats.for_path(src, cfg)
    opts = formats.options(fmt, cfg)
    text, encoding = read_document(src, formats.encodings(fmt, cfg))
    text, eol = split_terminator(text)
    facts = fmt.describe(text, opts)
    dnt = load_dnt(cfg)
    nodes, segments = fmt.parse(text, dnt, opts)

    # `prior_doc` rather than `load_doc`: extract is what migrates a state file
    # the current build refuses to read, so it must be able to read the
    # translations out of one first. It still refuses a file from a *newer*
    # build, because the next line overwrites it. `--reset` skips that read and
    # so overwrites it anyway — deliberately, and named in the message the newer
    # file raises, because "start over" is exactly what the flag means.
    stored = {} if reset else prior_doc(src, lang)
    # `prior_doc` refuses state from a newer build, and it runs first, so the
    # query below is never reached for one. `--reset` skips both, which is what
    # the flag means and what the refusal message promises.

    # The register is frozen onto the document, and a re-extract that does not
    # name one keeps the frozen value. Without this a forgotten `--tone` would
    # quietly return the document to the configured default — and since the
    # register entered the memory key, that takes every carryover and every
    # memory hit with it. `--reset` starts from `--tone` or config instead: it
    # does not read the state file at all, because it has to work on one this
    # build cannot read.
    tone = tone or stored.get("tone") or cfg.get("tone", DEFAULT_TONE)
    prior = no_carryover() if reset else prior_targets(src, lang)

    tm = load_tm(lang)
    reused, rejected = 0, 0
    #: What the carryover did that a person has to be told about. Segment ids,
    #: except `register`, which is a document-level fact. A dict rather than four
    #: more elements of the return tuple: the callers that ignore it — `cmd_run`
    #: and `POST /api/extract` — keep ignoring one thing, and the next entry
    #: costs a key rather than an arity change at every call site.
    notes = {"kept": [], "ambiguous": [], "replaced": [], "waived_source": [],
             "register": None}
    # Which stored entry each segment inherits, decided for the document at once:
    # two positions holding the same sentence can only be told apart by looking
    # at both, which is what a map of one entry per key could not do.
    # Divergence (25).
    inherited = prior.align(segments, tone)
    if stored.get("tone") and canonical_tone(stored["tone"]) != canonical_tone(tone) \
            and len(prior):
        # Said out loud because it is a silent destructive act otherwise, and the
        # contract tells a client to send `tone` on a re-extract button. Nothing
        # carries across a register change — deliberately, see
        # `store.prior_targets` — so this is the count of translations the
        # document is about to stop holding.
        notes["register"] = (stored["tone"], tone, len(prior))
    for seg in segments:
        # This document's own state first, then the memory. Both are proposals,
        # not results: reuse goes through `accept` for the same reason model
        # output does — the placeholder set is the one thing neither the pipeline
        # nor a reviewer can reconstruct, and a stale entry that keeps its key
        # while the mask configuration moves under it is the measured case.
        candidates = []
        carried, ambiguous = inherited[seg["id"]]
        if carried is not None:
            candidates.append(carried)
            if ambiguous:
                notes["ambiguous"].append(seg["id"])
        # **A memory hit competes only with wording a machine can make again.**
        # Divergence (27), closed 2026-09-01. The two proposals used to be tried
        # in order with no regard for who wrote the first, so a stored target
        # that no longer fits — with a banked wording behind it that does — was
        # replaced, and a person's sentence came back as `tm`: a provenance
        # nobody claimed, and not the one *Origin precedence* protects, so the
        # next unattended run could overwrite it. Needed no collision and no
        # race. Invariant 9 is the line — a machine draft is regenerable and a
        # person's sentence is not — and `store.is_regenerable_origin` is where
        # the taxonomy lives, beside the write guard that reads the same field.
        #
        # The lookup is skipped rather than the candidate dropped: a hit nothing
        # may use is a read of the memory nobody asked for.
        #
        # *Cost, accepted with the decision:* a broken machine draft no longer
        # gets out of the way of a banked wording that fits, so that segment
        # costs one repair call. What a person or an agent wrote is kept instead,
        # falls to the branch below, and is reported at `lx check` like any other
        # kept wording.
        hit = hit_origin = hit_slots = None
        hit_waived = False
        if carried is None or is_regenerable_origin(carried[1]):
            hit, hit_origin, hit_slots, hit_waived = tm_lookup(tm, seg, tone)
        if hit is not None and (hit_slots is not None or not _protected(seg, hit, dnt)):
            # No review state and no waiver on a memory hit, and that is one
            # design rather than two: the memory is wording banked from somewhere
            # else, a hold is one reviewer saying this segment is theirs to
            # finish, and a waiver is one reviewer answering a report on a
            # position they read. Carrying either in would speak for somebody
            # about a segment they have never seen. A hit *from* a waived record
            # is named instead, below, so the reader is told rather than left to
            # find out from a check they did not expect to fail.
            #
            # A line banked before the memory carried its own slot map is offered
            # only where a renumbering could not have moved it — see `_protected`.
            candidates.append((hit, hit_origin, None, False, hit_slots))
        for rank, (proposal, origin, review, waived, written_against) in enumerate(candidates):
            # The memory is tried even when this document's own target was
            # refused: the two can differ, and a good banked wording should not be
            # lost to a stale one sitting in front of it.
            # `slots=` is the map this wording's placeholders were written
            # against. A carryover knows it; a memory hit does not, and passing
            # `None` there is what says so rather than what forgets it.
            target, _why = accept(seg, proposal, lang, cfg, slots=written_against)
            if target is not None:
                seg["target"], seg["origin"], seg["status"] = target, origin, "translated"
                # A hold rides with the wording it was placed on. Dropped only
                # when another proposal took the segment — the wording the hold
                # was placed on is gone then — and by `--reset`, which reads no
                # prior state at all, which is what "start over" means.
                if review:
                    seg["review"] = review
                # The waiver rides with the wording it was granted on, and only
                # from this document's own prior state — a memory hit always
                # brings `False`, set where the candidate is built.
                if waived:
                    seg["waived"] = True
                if hit is not None and proposal is hit and hit_waived:
                    # The wording came out of the memory and the line says a
                    # reviewer had to waive it where it was banked. It arrives
                    # here unwaived on purpose, so `lx check` reports it and this
                    # reader decides for themselves — but arriving silently is
                    # what would make that a surprise instead of a handover.
                    notes["waived_source"].append(seg["id"])
                if carried is not None and rank:
                    # The carried entry is always the first candidate, so a later
                    # one winning means the memory answered over wording this
                    # document was already holding. Since 2026-09-01 that wording
                    # is always a machine's — the gate above is what makes it so
                    # — and the memory still holds what it replaced, so nothing
                    # is lost. Still counted, because a segment whose `origin`
                    # moved is a segment a reviewer may want to look at, and
                    # because this is the array a client watches to see the run
                    # narrow. Divergence (27), closed.
                    notes["replaced"].append(seg["id"])
                reused += 1
                break
        else:
            if candidates:
                rejected += 1
                if carried is not None:
                    # **`lx extract` does not delete wording this document
                    # already holds.** Every proposal was refused — the measured
                    # cause is a `config/dnt.txt` edit moving the mask
                    # configuration out from under banked wording, so the
                    # placeholder set no longer matches — and until 2026-08-17
                    # the segment came back with no target at all: a sentence
                    # somebody wrote, deleted by a re-parse.
                    #
                    # The rule it is judged by is the one `lx apply` already
                    # states. A person's, an agent's or a model's stored wording
                    # enters through a path that deliberately does not refuse,
                    # and is reported at `lx check` — where this one is an error
                    # on the `tags` rule — rather than rejected at the door. The
                    # acceptance gate exists to stop wording banked *elsewhere*
                    # from being written into a segment sight unseen; this
                    # wording is not elsewhere, it is what the document was
                    # already holding. Divergence (24).
                    #
                    # *Lost:* marking it with a second `review` value, which
                    # spends a vocabulary the contract advertises as closed on a
                    # state `lx check` already reports. *Lost:* deleting it and
                    # naming the ids on both surfaces, which is cheaper and
                    # answers "which sentence did I lose" with a list instead of
                    # with the sentence.
                    target, origin, review, waived, written_against = carried
                    seg["target"], seg["origin"], seg["status"] = target, origin, "translated"
                    if review:
                        seg["review"] = review
                    # A kept wording keeps its waiver too: it is the same wording
                    # at the same position, which is exactly what the waiver was
                    # granted over. What it does *not* do is silence the reason
                    # it was kept — an `extra` id or a dangling pair half is
                    # unwaivable at `checks.check_segment`, so the `tags` error
                    # that names this segment survives the waiver by
                    # construction.
                    if waived:
                        seg["waived"] = True
                    # **The kept wording keeps its provenance.** `save_doc` is
                    # about to write this segment with the *fresh* parse's
                    # `slots`, so without this the next extract would compare the
                    # new map against itself and accept the stale wording in
                    # silence — measured: the guard fires once and never again.
                    # Written only when the two differ, so the ordinary segment
                    # costs nothing.
                    if written_against and written_against != seg.get("slots"):
                        seg["target_slots"] = written_against
                    notes["kept"].append(seg["id"])

    doc = {
        # `doc_label`, not `os.path.relpath`: one spelling of one identity, on
        # every platform and on every surface that shows it. See its docstring.
        "version": __version__, "source": doc_label(src), "lang": lang,
        "tone": tone,
        # Which parser produced the skeleton, and which encoding the source was
        # in. Both are document-level facts, held beside `eol` for the same
        # reason: a segment must not carry them, and no later command should have
        # to re-derive them from a path that may since have been renamed. A state
        # file written before formats existed has neither key, and Markdown /
        # UTF-8 are the right defaults for one, because nothing else could have
        # written it.
        "format": fmt.name,
        "encoding": encoding,
        # The document's own line terminator, held here rather than in the
        # skeleton so the model and the reviewer never see it. A state file
        # written before this existed has no key, and "\n" is the right default
        # for one: text-mode reads had already deleted every CR.
        "eol": eol,
        "nodes": nodes, "segments": segments,
    }
    # Whatever the parser resolved by heuristic rather than by rule — for plain
    # text, the paragraph shape. Merged rather than nested so `lx extract` and a
    # future `lx status` can read one flat document; a format that adds a key
    # colliding with one above is the format's bug, and there are two formats.
    doc.update(facts)
    save_doc(src, lang, doc)
    return doc, reused, rejected, notes


def report_extract(src, lang, notes):
    """What the carryover did that a person has to be told about.

    One function, called by `lx extract` **and** by `lx run`, because `lx run`
    begins with `do_extract` and carries the same `--tone`. It lived inside
    `cmd_extract` for one afternoon, which was long enough for `lx run` to empty
    a reviewed book on a register change and print `0 reused · 2 to translate` —
    indistinguishable from a document being translated for the first time. Two
    surfaces of one product, each with its own idea of what is worth saying, is
    the shape `AGENTS.md` keeps naming.
    """
    if notes["kept"]:
        # Its own line rather than a field in the counts, because this one is not
        # a memory problem: these segments held a stored target that no longer
        # fits the document they were re-parsed from. Said loudly, because it is
        # wording somebody wrote — and until 2026-08-17 the wording was deleted
        # here rather than kept, with nothing printed but "stale memory hit(s)
        # refused", which names the memory rather than the sentence.
        ids = ", ".join(notes["kept"])
        _out(f"  {len(notes['kept'])} segment(s) kept a stored target whose placeholders no "
             f"longer match this document: {ids}. Nothing was lost — `lx render` writes each "
             f"one against the numbering it was written in, and `lx check` reports it: an "
             f"error where the placeholders no longer balance, a `numbering` warning where "
             f"they balance but have stopped meaning the same terms — and a warning either "
             f"way on a segment you have waived. Fix the wording, or "
             f"re-translate with "
             f"`lx translate {src} --lang {lang} --ids {','.join(notes['kept'])}` "
             f"(--overwrite-human if a person wrote it).")
    if notes["replaced"]:
        # The path (24) does not cover: a refused carryover with a memory hit
        # behind it that fits. Since 2026-09-01 the wording that gives way is
        # always a machine's and the memory still holds it, so this is a report
        # of a provenance that moved rather than of a sentence that went.
        _out(f"  {len(notes['replaced'])} segment(s) held a machine draft that no longer fits "
             f"this document, and a banked wording replaced it: "
             f"{', '.join(notes['replaced'])}. Their `origin` is now `tm`. Wording a person "
             f"or an agent wrote is never replaced this way — it is kept and reported above.")
    if notes["waived_source"]:
        # Named for the same reason `replaced` is: the segment comes back
        # `translated`, and nothing else on any surface would tell this reader
        # that the wording they just inherited only passes where somebody
        # overrode a rule. It arrives unwaived, so `lx check` will report it here
        # — this line is what makes that expected rather than a surprise.
        _out(f"  {len(notes['waived_source'])} segment(s) took a banked wording that was "
             f"waived where it was committed: {', '.join(notes['waived_source'])}. The "
             f"waiver did not travel with it — one reviewer's judgement about one position "
             f"is not one about this document. Read the wording. `lx check` reports it here "
             f"whenever the rule that was waived fires on this document too, and says "
             f"nothing when it does not, which is why this line is the only place the "
             f"handover appears.")
    if notes["ambiguous"]:
        # The half no alignment can fix: a run of identical paragraphs that
        # gained or lost a member has no evidence left about which wording
        # belongs where. Named rather than guessed at in silence. Divergence (26).
        _out(f"  {len(notes['ambiguous'])} segment(s) repeat a sentence this document holds "
             f"elsewhere, and that run changed size, so which stored wording belongs to which "
             f"position is not established: {', '.join(notes['ambiguous'])}. Check their "
             f"wording and `origin`.")
    if notes["register"]:
        was, now, held = notes["register"]
        # A register change carries nothing over, deliberately — and until
        # 2026-08-17 it said nothing at all while emptying a reviewed book.
        _out(f"  the register moved from {was} to {now}, and translations do not cross "
             f"registers: the {held} this document held are not in it any more. They are in "
             f"the last render, and in `.lx/tm.{lang}.jsonl` if it was committed.")


def cmd_extract(args, cfg):
    doc, reused, rejected, notes = do_extract(
        args.src, args.lang, cfg, args.tone, args.reset)
    pending = sum(1 for s in doc["segments"] if s["status"] == "pending")
    _out(f"{args.src} [{args.lang}] -> {db_path()}")
    line = f"  segments {len(doc['segments'])} | reused {reused} | pending {pending}"
    # Only when it happened. "Proposal" rather than "memory hit", which is what
    # this said until 2026-08-17 and was wrong half the time: the count is what
    # the acceptance path refused, and a refusal is as often this document's own
    # stored wording as a banked entry — which is the line below, and is why
    # naming the memory here sent a reader to the wrong file.
    if rejected:
        line += f" | {rejected} stale proposal(s) refused"
    # Likewise only when it is not the default: the register decides both the
    # brief and which half of the memory answers, so a document that is in one
    # should say so on the line that reports what carried over.
    if canonical_tone(doc["tone"]) != DEFAULT_TONE:
        line += f" | tone {doc['tone']}"
    _out(line)
    report_extract(args.src, args.lang, notes)
    # Everything the parse decided rather than read. Printed only when it is not
    # the ordinary answer, so a Markdown project's output does not change at all
    # — but printed *always* for a document whose encoding or paragraph shape was
    # guessed, because both are heuristics that can be wrong and both are
    # cheapest to catch here. Measured: an ordinary Windows Big5 novel can be
    # read as Latin-1 by a mis-ordered candidate list, and the mojibake is
    # durable once `lx commit` banks it.
    guessed = []
    if doc.get("encoding") and doc["encoding"] != "utf-8":
        guessed.append(f"encoding {doc['encoding']}")
    if doc.get("paragraph_mode"):
        guessed.append(f"paragraphs {doc['paragraph_mode']}")
    if guessed:
        _out(f"  read as {doc.get('format', 'markdown')} · " + " · ".join(guessed)
             + " — set `formats` in lx.config.json if that is wrong")


# ── todo ───────────────────────────────────────────────────────────────────

def checked_limit(limit, what="limit"):
    """``limit`` as an integer bound, or a refusal saying why it is not one.

    **Separate from applying it**, and that separation is the decision: a
    malformed field is malformed whether or not this call would have reached
    it. `do_select` checks the value before `ids` short-circuits, so
    `{"ids": [...], "limit": "5"}` is refused rather than quietly accepted on
    the strength of a precedence rule the client has not hit yet — otherwise the
    bug surfaces later, on the day they stop sending `ids`, as a run that
    translates a whole book.

    Three refusals, and each was reachable before this existed:

    * `bool`. `isinstance(True, int)` is true, so `{"limit": true}` on the wire
      sliced to exactly one segment and `lx translate --limit true` was an
      argparse error only by luck of `type=int`.
    * a negative. `out[:-5]` is *everything except the last five* — measured on
      the parent build `67629fd`: `lx translate --limit -5` on a 100-segment
      document translated 95 of them and exited 0.
    * anything that is not an integer at all, which on the wire is any JSON
      value a client sends.

    `0`, `False`, `None` and an absent value are one thing and mean unbounded,
    which is what every caller's default already was. `False` is accepted where
    `True` is refused because `body.get("limit")` yields it for a client sending
    `false`, and "no bound" is a reading that value has; `true` has none.

    A `ValueError` subclass rather than a message at each surface, so the CLI
    answers one sentence and exit 2 and `web/server.py` answers the `400` its
    contract states — and so neither can walk around it, which is the reason
    `do_apply`'s empty-target refusal lives in `do_apply`.
    """
    if limit is None or limit is False:
        return 0
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise UnusableTarget(
            f"`{what}` is a whole number of segments, and this request sent "
            f"{type(limit).__name__}. It says how much of the document goes to "
            f"the model this time; 0 or nothing at all means all of it.")
    if limit < 0:
        raise UnusableTarget(
            f"`{what}` is {limit}, and a negative bound is not a smaller run — "
            f"it would take every segment except the last {abs(limit)}. Pass a "
            f"positive number, or 0 for the whole document.")
    return limit


def bounded(segments, limit, what="limit"):
    """``segments`` capped at ``limit``, with ``limit`` refused if it is not one.

    **The cap and its rule live in one function**, called by `pending_segments`
    for `lx todo` and by `do_select` for every run. Two spellings of `[:limit]`
    is how the two came to disagree about what a bad value means.

    The rule itself is `checked_limit`, which every caller reaches through this
    one — see its docstring for what a bound may be and why the check is
    separable from the slice.
    """
    limit = checked_limit(limit, what)
    return segments[:limit] if limit else segments


def pending_segments(doc, include_all=False, limit=0):
    """The draft queue, and what `lx todo` hands an agent.

    Held segments are excluded through the one shared helper, and before the
    limit rather than after it: filtering afterwards would let a run of held
    segments eat a `--limit 20` and hand back four.
    """
    out = [s for s in workable(doc["segments"])
           if include_all or s["status"] == "pending"]
    return bounded(out, limit)


def cmd_todo(args, cfg):
    """Emit pending segments, and everything the translator has to be told.

    ``voice`` and ``voice_notes`` are what make an agent a peer of the model
    rather than a third of the pipeline working blind. `AGENTS.md` treats an API
    model, an agent in its own context and a human as three equal sources of a
    translation, and until this landed the register brief reached only
    `translate_segments` — so an agent produced documentation prose for a novel
    and `lx commit` banked it under the literary key anyway. Both fields are the
    *same strings* the model path assembles, from the same two functions in
    `translate`, so the two paths cannot drift into briefing differently.

    Both keys are always present, empty rather than absent when there is nothing
    to say: a consumer that has to branch on a missing key breaks the first time
    a project has no style sheet, and HANDOFF-203 and HANDOFF-207 will freeze
    this shape.
    """
    # Lazily, the way `do_extract` imports `accept`: importing `translate` pulls
    # in the provider stack, and `lx todo` is the command that exists precisely
    # because nobody here is calling a model.
    from .translate import brief, mentions, style_notes, style_preamble_text

    doc = load_doc(args.src, args.lang)
    glossary = load_glossary(cfg)
    style_preamble, style_blocks = load_style(cfg)
    segments = pending_segments(doc, args.all, args.limit)
    items = []
    for seg in segments:
        item = {"id": seg["id"], "kind": seg["kind"], "text": seg["masked"]}
        low = seg["masked"].lower()
        # A row with no target is a candidate `lx terms` proposed and nobody has
        # decided yet. Handing an agent `{"term": "Ashcombe", "use": ""}` asks it
        # to render the name as nothing, so an unfinished row stays silent —
        # which is what `checks.check_segment` already does with one.
        hints = [{"term": r["source"], "use": r["target"]} for r in glossary
                 if r["target"] and mentions(low, r["source"])]
        if hints:
            item["glossary"] = hints
        if seg.get("issues"):
            item["fix"] = seg["issues"]
        items.append(item)
    # Selected against the whole emitted set rather than per segment — the same
    # rule `translate.style_notes` applies to a batch, so an agent handed twenty
    # paragraphs sees exactly what the model would have seen for the same twenty.
    notes = style_notes(segments, style_blocks)
    _out(json.dumps({
        "source": doc["source"], "lang": doc["lang"], "tone": doc["tone"],
        "voice": "\n\n".join(p for p in (brief(doc["lang"], doc["tone"]),
                                         style_preamble_text(style_preamble)) if p),
        "voice_notes": [{"names": b["names"], "notes": b["notes"]} for b in notes],
        "rules": "placeholders \u27e6n\u27e7 are opaque; copy them verbatim, "
                 "reorder if grammar needs it, never invent or drop them",
        "segments": items,
    }, ensure_ascii=False, indent=2))


# ── terms ──────────────────────────────────────────────────────────────────

#: The letters a word can be made of. ASCII plus Latin-1 Supplement through
#: Latin Extended-B, minus `×` and `÷`, which sit inside that block and are not
#: letters. `mdparse` already reaches for the same range to decide whether a
#: block has translatable text in it; an ASCII-only class cut `René` down to
#: `Ren` and split `Müller` into `M` and an invisible `ller`, and a novel in
#: English is full of names that are not.
_LETTER = "A-Za-zÀ-ÖØ-öø-ɏ"

#: A word as this command counts one: letters, with an internal apostrophe or
#: hyphen kept inside the token, so `O'Brien` and `Anne-Marie` are one candidate
#: rather than two halves of nothing.
_WORD_RE = re.compile(rf"[{_LETTER}]+(?:['’-][{_LETTER}]+)*")

#: A possessive, trimmed off the end of a candidate. `Ashcombe's carriage` and
#: `Ashcombe walked` are one name, and counting them as two splits the evidence
#: for a minor character across two rows that each fall under the threshold.
_POSSESSIVE_RE = re.compile(r"['’][Ss]\Z")

#: What ends a sentence. `…` is here because a name after `…` is at a
#: sentence start exactly as one after `...` is, and only the second spelling
#: would be caught by the full stop.
_SENTENCE_END = ".!?…"

#: An opening quotation mark. It includes `'` and `’`, which are apostrophes far
#: more often than quotes — safely, because the rule below reads only the
#: character *adjacent* to the token: a possessive apostrophe is followed by a
#: space, so `the Smiths' Manor` never reaches this set, while `He said, 'The
#: door…'` does and is suppressed like its double-quoted twin.
_OPEN_QUOTES = "\"“‘«'’"

#: An exclamation or question mark kept inside a closing quote, which does *not*
#: end the sentence the attribution belongs to. English punctuates dialogue as
#: `"Run," Ashcombe said.` — comma, handled by adjacency — but as `"Run!"
#: Ashcombe said.`, keeping the mark. A full stop is deliberately absent: `"Run."
#: Ashcombe left.` is two sentences, and treating it as one would suppress a real
#: sentence opener for nothing.
_DIALOGUE_END_RE = re.compile(r"^[!?…]+[\"”’'»]")


def _sentence_start(gap, previous, abbreviations):
    """Does a token begin a sentence, given the text between it and the one before?

    `gap` is that text verbatim, `previous` the word token in front of it. This
    is the whole substance of the command: a capitalized token at a sentence
    start carries no evidence that it is a proper noun, because English
    capitalizes every sentence's first word.

    Four rules, in the order they fire.

    **An opening quote wins, and only when it is adjacent to the token.** Dialogue
    opens a sentence, so `He said, "The door…"` must suppress `The` — the comma is
    not a terminator and nothing else would. But `"` is also a *closing* quote,
    and `"Run," Ashcombe said.` is the commonest shape in a novel, where treating
    the `"` as an opening would suppress the one name in the line. The gap tells
    them apart by position: an opening quote is the last character before the
    token, a closing one has whitespace after it. Measured on both shapes.

    **A `!` or `?` inside a closing quote does not end the attribution's
    sentence.** `"Run!" Ashcombe said.` is how English punctuates it — the mark
    stays inside the quote and the attribution continues the sentence — so
    without this a character attributed only that way has no mid-sentence
    occurrence anywhere. A full stop is excluded on purpose: `"Run." Ashcombe
    left.` is genuinely two sentences, because English writes a comma, not a
    period, when an attribution follows. `"Run!" She turned away.` is the
    residual false positive, and it is the accepted one — the two shapes cannot
    be told apart without a table of attribution verbs, which is judgement.

    **An abbreviation's full stop does not end a sentence.** `Mr. Darcy` is why:
    without this, a character named only after an honorific has no mid-sentence
    occurrence anywhere and is never proposed. The exception is narrow on purpose
    — only a `.` that opens the gap, only after a word in the configured list.

    **Otherwise, any sentence terminator in the gap.** This over-suppresses after
    an unlisted abbreviation, and that is the accepted cost: the failure is a
    missing candidate for a name that occurs mid-sentence nowhere else, and the
    list is configuration precisely so a project can extend it.
    """
    if gap and gap[-1] in _OPEN_QUOTES:
        return True
    dialogue = _DIALOGUE_END_RE.match(gap)
    if dialogue:
        gap = gap[dialogue.end():]
    elif previous in abbreviations and gap.startswith("."):
        gap = gap[1:]
    return any(ch in _SENTENCE_END for ch in gap)


def candidate_terms(segments, min_count=2, abbreviations=(), stopwords=()):
    """Rank runs of capitalized words by frequency. ``[{source, count, …}]``.

    Reads `seg["masked"]`, never the raw source, so code spans, URLs and
    do-not-translate terms are already `⟦n⟧` and cannot be proposed as names —
    which is also why this command does not have to re-solve masking.

    **A candidate is a maximal run of capitalized tokens separated by exactly one
    space.** A single token is a run of length one. *Lost:* also emitting each
    word of a longer run as its own candidate, which turns `Ashcombe Hall` into
    three rows and a two-hundred-name novel into six hundred. A name that also
    stands alone is already its own run wherever it does.

    *Lost:* joining a run across any whitespace, so that a line break inside a
    wrapped paragraph does not split `Ashcombe Hall`. The glossary matches on the
    literal source string — `checks.check_segment` and `translate._glossary_hints`
    both search for it with a word-boundary regex — so a run joined across a
    newline would propose a row that can never fire. A row that cannot fire is
    worse than two rows that can.

    **A run whose first token opens a sentence also records its tail.** `Then
    Ashcombe spoke.` is one run, `Then Ashcombe`, and it opens the sentence — so
    without this the maximal-run rule swallows the one occurrence of `Ashcombe`
    that was genuinely mid-sentence, and a name that follows `Then`, `But` or
    `And` loses evidence it actually had. Position is per token: the tail did not
    open the sentence, whatever the head did.

    **A candidate needs one occurrence that is not sentence-initial.** That is the
    filter; `min_count` is only a floor on how often it was seen at all. Requiring
    `min_count` *mid-sentence* occurrences was the alternative and loses: a
    character name leads sentences constantly, so a name seen forty times with one
    mid-sentence occurrence is a real name and would have been dropped. The bias
    is deliberate — a spare row costs one keystroke, a missing one costs the
    discovery this command exists for.
    """
    abbreviations, stopwords = set(abbreviations), set(stopwords)
    counts, mid = Counter(), Counter()
    examples = {}

    def record(words, initial, sid):
        source = " ".join(words[:-1] + [_POSSESSIVE_RE.sub("", words[-1])])
        # An honorific is not a proper noun, and it is the one word in the
        # abbreviation list guaranteed to appear mid-sentence — `said Mr. Darcy`
        # — so left in it outranks every real name in the ranking it is supposed
        # to be helping. A single character is dropped for a harder reason: a
        # glossary row `J` fires on every segment containing a bare J, so it is
        # not enforceable terminology under any wording.
        if source in stopwords or source in abbreviations or len(source) < 2:
            return
        counts[source] += 1
        if initial:
            return
        mid[source] += 1
        # Only mid-sentence occurrences are worth pointing a reviewer at: they
        # are the evidence. Three, because a list of four hundred segment ids
        # for a main character is not an example of anything.
        seen = examples.setdefault(source, [])
        if sid not in seen and len(seen) < 3:
            seen.append(sid)

    def flush(words, initial, sid):
        record(words, initial, sid)
        if initial and len(words) > 1:
            record(words[1:], False, sid)

    for seg in segments:
        text = seg.get("masked") or ""
        tokens, end, previous = [], None, ""
        for m in _WORD_RE.finditer(text):
            word = m.group(0)
            if end is None:
                # The first token of a segment starts a sentence. A segment is a
                # whole block — a paragraph, a heading, a cell — so nothing that
                # came before it is in the same sentence.
                initial, joins = True, False
            else:
                gap = text[end : m.start()]
                initial = _sentence_start(gap, previous, abbreviations)
                joins = gap == " "
            tokens.append((word, word[0].isupper(), initial, joins))
            end, previous = m.end(), word

        run, run_initial = [], False
        for word, capitalized, initial, joins in tokens:
            if run and capitalized and joins:
                run.append(word)
                continue
            if run:
                flush(run, run_initial, seg["id"])
            run, run_initial = ([word], initial) if capitalized else ([], False)
        if run:
            flush(run, run_initial, seg["id"])

    # `target` is carried as an explicit empty string rather than left out. A
    # consumer of `--json` should be able to assert the emptiness — it is this
    # command's contract, not a field nobody got round to filling — and an
    # absent key asserts nothing.
    rows = [{"source": s, "target": "", "count": n,
             "mid_sentence": mid[s], "examples": examples[s]}
            for s, n in counts.items() if n >= min_count and mid[s]]
    # Frequency first, then the term itself: two candidates seen the same number
    # of times must come out in the same order on every machine, or `--append`
    # writes a different glossary depending on who ran it.
    rows.sort(key=lambda r: (-r["count"], r["source"]))
    return rows


def append_glossary_rows(cfg, rows):
    """Add rows to the glossary, leaving every byte already in it alone.

    The file is rewritten through a temporary one rather than opened in append
    mode: the existing bytes are *concatenated*, never reparsed or reformatted,
    so "never rewrite or reorder an existing row" holds by construction rather
    than by care, and a crash halfway through leaves a hand-maintained file
    intact. `dump_json` already pays the same price for the state file.

    No CSV quoting, because a candidate cannot need it: a run of capitalized
    tokens joined by single spaces contains no comma and cannot begin with `#`.
    `load_glossary` splits on `,` with no quoting at all, so a writer that could
    emit a comma would be writing rows that file cannot read back.

    **Invariant 11 is not applied to `cfg["glossary"]` here, and that is a
    decision.** The path is read out of a configuration file, which the invariant
    names as untrusted, and this is the first thing in the tree that *writes* to
    such a path. It is exempt on the invariant's own stated ground — configuration
    is written by hand — and confining it now would be worse than useless in two
    ways: `load_glossary` reads the same path unconfined, so the command could
    read a glossary it refused to append to, and a project that legitimately
    shares `../shared/glossary.csv` between two books would break with no decision
    recorded. The exemption ends the moment configuration becomes writable over
    HTTP, and it binds every path-valued configuration key rather than this one:
    `glossary`, `dnt`, `style` and `output_pattern` are confined at use time or
    they are not writable over HTTP. Recorded in `docs/decisions.md`, 2026-08-02,
    "Terminology is discovered by suppressing sentence-initial capitals" —
    tracked, unlike the work packages that also carry it.
    """
    if not rows:
        return 0
    path = cfg.get("glossary", "config/glossary.csv")
    tmp = path + ".tmp"
    # The read is inside the guard with the write. A glossary that cannot be
    # opened and one that cannot be replaced are the same problem to the person
    # holding the keyboard, and a traceback out of either is the same unhelpful
    # answer — so there is one refusal, not one plus a crash.
    try:
        existing = GLOSSARY_HEADER.encode("utf-8")
        if os.path.exists(path):
            with open(path, "rb") as f:
                existing = f.read()
        # The file's own terminator, not this platform's. `lx init` writes LF, but
        # a glossary is hand-maintained and an editor on Windows may well have
        # saved it as CRLF — appending LF rows to that leaves a file with both, in
        # the one place this function exists to leave alone. `load_glossary` reads
        # either.
        eol = b"\r\n" if b"\r\n" in existing else b"\n"
        if existing and not existing.endswith(b"\n"):
            # A hand-edited file whose last line has no terminator. Without this
            # the first appended row would be glued onto the end of an existing
            # one, which is the one way a pure append can still destroy a row.
            existing += eol
        body = b"".join(f"{r['source']},,,error".encode() + eol for r in rows)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(existing + body)
        os.replace(tmp, path)
    except OSError as e:
        # Read-only, or open in a spreadsheet, which is not an exotic state for a
        # CSV a person maintains — it is Tuesday. Unhandled, `os.replace` ended
        # the command in a traceback and exit 1; every other refusal in this
        # project is one sentence and exit 2. What is reachable differs by
        # platform — POSIX replaces a read-only file happily, because it asks the
        # directory rather than the file — so the guard covers the operation
        # rather than the cause.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise GlossaryWriteError(
            f"could not use {path} ({e.strerror or e}). That is the file "
            f"`lx terms --append` adds rows to — check the path in lx.config.json, and "
            f"that the file is not read-only or open in another program. It is "
            f"unchanged.") from None
    return len(rows)


def do_terms(src, lang, cfg, min_count=None, append=False):
    """Propose glossary rows from a document's own source text.

    **The target column is left empty, and that is the line this command does not
    cross.** Which characters render `Ashcombe` as 灰岸 rather than 阿什科姆 is
    judgement, and invariant 4 keeps judgement in a person's hands; a command that
    invented the target would have moved it into `checks.py`'s input one step
    upstream. Extraction is mechanically decidable and belongs in code. See
    `docs/decisions.md`, 2026-08-02, "Terminology is discovered by suppressing
    sentence-initial capitals, and the target column stays empty" — which is the
    entry that states this rule; D3 of 2026-07-29 decided only that the discovery
    problem belongs to a command rather than to the translation memory.

    Candidates already in the glossary are dropped in every mode, not only under
    `--append`: what is missing is the whole question, and a proposal list that
    repeats what the project already decided is a list nobody reads twice.
    Case-insensitively, because that is how `check_segment` and
    `_glossary_hints` match a row against source text.
    """
    primary = str(cfg.get("source_lang", "en")).split("-")[0].lower()
    if primary != "en":
        raise UnsupportedSource(
            f"`lx terms` reads English source text, and source_lang is "
            f"{cfg.get('source_lang')!r}. The whole rule is English capitalization, so on "
            f"another source language it would report success and propose nothing. Set "
            f"source_lang to \"en\" in lx.config.json if the source really is English, or "
            f"fill config/glossary.csv by hand.")

    doc = load_doc(src, lang)
    opts = cfg.get("terms") or {}
    if min_count is None:
        min_count = opts.get("min_count", 2)
    found = candidate_terms(doc["segments"], min_count,
                            opts.get("abbreviations", ()), opts.get("stopwords", ()))
    known = {r["source"].lower() for r in load_glossary(cfg)}
    fresh = [r for r in found if r["source"].lower() not in known]
    return {
        "source": doc["source"], "lang": doc["lang"],
        "glossary": cfg.get("glossary", "config/glossary.csv"),
        "min_count": min_count,
        "known": len(found) - len(fresh),
        "appended": append_glossary_rows(cfg, fresh) if append else 0,
        "terms": fresh,
    }


def cmd_terms(args, cfg):
    report = do_terms(args.src, args.lang, cfg, args.min_count, args.append)
    if args.json:
        _out(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if args.append:
        _out(f"{report['source']} [{report['lang']}]  {len(report['terms'])} new "
             f"candidate(s), {report['known']} already known")
        _out(f"appended {report['appended']} row(s) to {report['glossary']} — fill in "
             f"the target column; a row with an empty target enforces nothing")
        return
    # Comment lines rather than plain ones, because `lx terms doc.md --lang zh-TW
    # > terms.csv` is the invocation this default exists for and `load_glossary`
    # skips a line starting with `#`. The summary rides along inside a file that
    # is still a valid glossary fragment.
    _out(f"# {len(report['terms'])} candidate term(s) from {report['source']}, seen "
         f"{report['min_count']}+ time(s)"
         + (f"; {report['known']} already in {report['glossary']}" if report["known"] else ""))
    _out("# fill in the target column; a row with an empty target enforces nothing")
    for row in report["terms"]:
        _out(f"{row['source']},,,error")


# ── apply ──────────────────────────────────────────────────────────────────

def do_apply(src, lang, cfg, incoming, origin="agent", base=None, over_human=False):
    """Write reviewed targets.

    ``(applied, unknown, stored, conflicts, refused)``. ``stored`` is
    ``{id: {"text", "token"}}`` for what was written and ``conflicts`` the same
    shape for what lost a lost-update check, so a caller never has to re-read the
    document to find out what it now holds. ``refused`` is separate and names the
    ids an ``llm:*`` ``origin`` was not allowed to overwrite — separate because
    `docs/contracts/workbench-http.md` defines ``conflicts`` as "refused because
    its ``base`` token did not match", and folding a second meaning into it would
    have been a silent contract change the day any endpoint passed an origin
    other than ``human``. `/api/save` hardcodes ``human``, so on the wire it is
    always empty and is not projected. That readback is what
    removes the save-then-refetch-the-whole-book loop on a five-thousand-segment
    novel, and it is what gives a conflict presentation something authoritative
    to diff against.

    **An empty target is refused, and the refusal is here rather than at the
    endpoint.** `AGENTS.md` records `lx apply` as the deliberate exception to
    refusal — a person's words are reported at `lx check`, not rejected at the
    door — and that exception protects *content* from a mechanical rule. An empty
    string is not content; it is the absence of it. Left storable, it combines
    with status-derived-from-text and the origin precedence scheduled next into a
    segment every run selects, no writer may write and `lx check` can never pass:
    a human clears a segment, the draft pass selects it on `pending`, the repair
    pass selects it on the `missing` error, and both writes are refused for being
    `llm:*` over `human`. Refusing at the door makes that state unreachable by
    construction rather than guarded against in three predicates. To have a
    segment translated again, run it: `lx translate --ids <id>`. See
    ``docs/decisions.md``, 2026-08-14.

    Whole-request, before anything is written, so a save carrying one blank has
    not half-happened — and only for ids that name a segment, because an id that
    names none is ignored rather than refused, which is what `unknown` already
    means here.

    **``base`` is the lost-update check**, ``{id: token}`` from
    :func:`store.target_token`. An id present in it is written only if the stored
    target still hashes to the token the caller was shown; otherwise nothing is
    written for that id and the current text and token come back in
    ``conflicts``. An id absent from ``base`` is written unconditionally, which
    is what keeps `lx apply` and any client that has not opted in behaving
    exactly as before. Reported in the body rather than as a status, because one
    request can carry a hundred segments and a status code cannot say which of
    them lost.

    **The check that counts is inside the write**, not here. The comparison below
    runs against the snapshot this call loaded, which is a good cheap filter and
    is not a guarantee: two writers whose reads both land before either write both
    pass it. So the previous target travels to :func:`store.save_segments` as
    ``expect`` and the write is a compare-and-swap in one statement. Measured
    2026-08-14 with two threads — before the swap, both were told
    ``applied: 1, conflicts: {}`` and one text was gone.
    """
    doc = load_doc(src, lang)
    by_id = {s["id"]: s for s in doc["segments"]}
    # Shape first, and refused rather than ignored. `sid in base` on a string is a
    # substring test, so a client that sent `base` in the wrong JSON shape had its
    # lost-update protection silently switched off while believing it had asked
    # for it — the one guarantee this field exists to give.
    if base is not None and not isinstance(base, dict):
        raise UnusableTarget(
            f"`base` is a map of segment id to the token that segment was shown with, and "
            f"this request sent {type(base).__name__}. Send that map, or omit `base` "
            f"entirely to write without the lost-update check.")
    bad = sorted(sid for sid, text in incoming.items()
                 if sid in by_id and not isinstance(text, str))
    if bad:
        raise UnusableTarget(
            f"{', '.join(bad)}: a target is text, and this request sent "
            f"{', '.join(sorted({type(incoming[s]).__name__ for s in bad}))}. Nothing was "
            f"written. A number or a list here is a client building the payload wrongly, "
            f"not a translation this build should try to store.")
    blank = sorted(sid for sid, text in incoming.items()
                   if sid in by_id and not text.strip())
    if blank:
        raise UnusableTarget(
            f"{', '.join(blank)}: an empty target is a rejected input here, not a stored "
            f"result — it would leave nothing to review while taking the segment out of "
            f"the queue that would have it retranslated. Write the wording you want, or "
            f"have the model do this one again: "
            f"`lx translate {src} --lang {lang} --ids {','.join(blank)}"
            # Named only when it is needed, and it is needed exactly when the
            # segment already holds a person's words — which is the common case
            # here, since clearing one is a reviewer's act. Without it the
            # sentence sends them to a command origin precedence then refuses,
            # which is the two halves of this feature contradicting each other.
            f"{' --overwrite-human' if any(by_id[s].get('origin') == HUMAN for s in blank) else ''}`.")
    unknown, changed = [], []
    stored, conflicts, expect = {}, {}, {}
    for sid, text in incoming.items():
        seg = by_id.get(sid)
        if not seg:
            unknown.append(sid)
            continue
        if base and sid in base:
            current = target_token(seg.get("target"))
            if base[sid] != current:
                conflicts[sid] = {"text": seg.get("target") or "", "token": current}
                continue
            # Captured before the line below overwrites it. This is what makes the
            # check real: the comparison above is against a snapshot read in an
            # earlier transaction, and `save_segments` re-checks this value inside
            # the write itself.
            expect[sid] = seg.get("target")
        # Half of `accept`, and only that half. A person's or an agent's words
        # are still never refused here — that asymmetry is deliberate and
        # `docs/decisions.md`, 2026-07-29, records why — but the blanks a segment
        # opens and closes with are the host syntax's, not the translator's, and
        # a reviewer retyping a paragraph in a textarea does not reliably
        # reproduce the four spaces that keep it inside its list item. Leaving
        # this side alone made one document render differently depending on which
        # of the three equal sources produced its target.
        #
        # No `.strip()` of its own: `reseat_outer_blanks` does that, and a second
        # one here is dead weight. Measured over 327600 combinations — every
        # subset of the zh-TW ops x 26 body shapes x 15 leading runs x 15 trailing
        # runs x 7 source shapes — with zero differences, after the mutation pass
        # found the two strips could each be deleted with the suite still green.
        #
        # `keep_added_indent` is where this path stops being `accept`'s: an
        # English source has no leading run, so the strict form deletes the
        # U+3000 pair a zh-TW reviewer types at the head of a paragraph — and
        # after it, no surface in the pipeline could produce an indented Chinese
        # paragraph at all. Adversarial review, 2026-08-03.
        seg["target"] = reseat_outer_blanks(
            seg["masked"], normalize(repair_placeholders(text), lang, cfg),
            keep_added_indent=True)
        # Derived from the text, never asserted by the act of writing. `status`
        # is the draft queue's selection predicate and not a progress display, so
        # marking an empty segment `translated` did not merely miscount it — it
        # removed it from the queue, and a reviewer's "this needs redoing" quietly
        # meant the opposite. Unreachable now that the refusal above runs first,
        # and written this way so the class of defect is closed rather than the
        # instance: it is also what makes `status == "translated"` and the two
        # non-empty-target counters agree by construction rather than by
        # coincidence. `docs/contracts/workbench-http.md` (14).
        seg["status"] = "translated" if seg["target"].strip() else "pending"
        seg["origin"] = origin
        seg.pop("issues", None)
        changed.append(seg)
        stored[sid] = {"text": seg["target"], "token": target_token(seg["target"])}
    # The segments this call touched, not the whole document: apply is what the
    # workbench calls on every keystroke-sized save, and rewriting a novel's
    # skeleton to record one edited paragraph is the amplification the state
    # layer moved to SQLite to remove.
    _written, stale, refused = save_segments(src, lang, changed, expect=expect,
                                            over_human=over_human)
    for sid in refused:
        stored.pop(sid, None)
    if stale:
        # The row moved between this call's read and its write. One extra read,
        # and only when that actually happened, so the conflict carries the text
        # the winner left behind rather than the one this call was shown.
        fresh = {s["id"]: s for s in load_doc(src, lang)["segments"]}
        for sid in stale:
            text = (fresh[sid].get("target") or "") if sid in fresh else ""
            conflicts[sid] = {"text": text, "token": target_token(text)}
            stored.pop(sid, None)
    return len(stored), unknown, stored, conflicts, sorted(refused)


def do_hold(src, lang, cfg, ids, held=True):
    """Hold segments out of every queue that selects work, or lift the hold.

    ``(applied, unknown)``. An id that names no segment is ignored rather than
    refused, which is what ``unknown`` means everywhere else on this surface.

    **Holding requires a non-empty target**, whole-request and before anything is
    written, which is what makes it compose with status-derived-from-text rather
    than fight it. A hold on an untranslated segment would say "leave this one to
    me" about a segment nobody has written yet, and would take it out of the
    draft queue by a route that queue cannot see — the same shape as the empty
    target :func:`do_apply` refuses, and refused here for the same reason.
    Lifting a hold has no such requirement: undoing something must never be
    harder than doing it.

    Lifting is this command's own act and never a side effect of a save. A
    reviewer editing a held segment keeps the hold — `do_apply` carries the field
    through untouched — because the two answer different questions, and a save
    that quietly released a hold would return the segment to the model's queue at
    the moment its wording changed.
    """
    if not isinstance(held, bool):
        raise UnusableTarget(
            f"`held` is true or false, and this request sent "
            f"{type(held).__name__}. A string or a null here is a client building "
            f"the payload wrongly — `null` would read as false and *release* a "
            f"hold, which is the opposite of the default.")
    if ids is None:
        ids = []
    if isinstance(ids, str) or not isinstance(ids, (list, tuple)):
        raise UnusableTarget(
            f"`ids` is a list of segment ids, and this request sent "
            f"{type(ids).__name__}. A bare string would be read one character at "
            f"a time and answer `applied: 0` while looking like it worked.")
    ids = [str(sid).strip() for sid in ids]
    ids = [sid for sid in ids if sid]
    doc = load_doc(src, lang)
    by_id = {s["id"]: s for s in doc["segments"]}
    wanted = [sid for sid in ids if sid in by_id]
    unknown = [sid for sid in ids if sid not in by_id]
    if held:
        blank = sorted(sid for sid in wanted if not (by_id[sid].get("target") or "").strip())
        if blank:
            raise UnusableTarget(
                f"{', '.join(blank)}: there is nothing here to hold — a hold says the "
                f"wording is yours to finish, and these segments have no wording yet. "
                f"Translate them first, or leave them in the queue: "
                f"`lx translate {src} --lang {lang} --ids {','.join(blank)}`.")
    return save_review(src, lang, {sid: (HELD if held else None) for sid in wanted}), unknown


def cmd_hold(args, cfg):
    ids = [s for s in args.ids.split(",") if s]
    applied, unknown = do_hold(args.src, args.lang, cfg, ids, held=not args.lift)
    verb = "released" if args.lift else "held"
    _out(f"{verb} {applied} segment(s)"
         + (f"; unknown ids ignored: {unknown}" if unknown else ""))


def do_waive(src, lang, cfg, ids, waived=True):
    """Answer these segments' reports, or take the answer back. ``(applied, unknown)``.

    The seam `lx waive` / `lx unwaive` and `POST /api/waive` share. A waiver says
    a person read what `lx check` reports on this wording and decided the wording
    is right — so the rules a reviewer's judgement can overrule are reported at
    *warn* on that segment instead of failing the build. It silences nothing:
    every issue is still in the report, still in ``by_rule``, and a ``waived``
    warning names the segment.

    **What it cannot reach is the half a reviewer cannot be right about.** An
    issue is waivable or not where it is raised, beside its severity
    (`checks.check_segment`), and the unwaivable ones are those that report the
    substituted *bytes* are malformed rather than that the wording may be wrong:
    the placeholder pair rules, containment, host escaping, the invented carriage
    return, and a `tags` mismatch carrying an id the segment has no slot for or
    dropping one half of a pair. Measured 2026-09-03: without that last clause a
    wording dropping only ``⟦2⟧`` of ``⟦1⟧very⟦2⟧`` gets `pair_problems() == []`,
    would have been waived, and renders an ``<em>`` that never closes.

    **Waiving requires a non-empty target**, whole-request and before anything is
    written — the rule :func:`do_hold` follows, for the same reason and with one
    more of its own. A waiver on an untranslated segment would answer a report
    nobody has read on wording nobody has written; and it would aim at
    ``missing``, which is unwaivable anyway, so the request could only ever be a
    mistake. Lifting has no such requirement: undoing must never be harder than
    doing.

    The waiver is pinned to the wording rather than to the position, and
    structurally rather than by a fingerprint recomputed on read:
    `store.save_targets` drops it on every write and `store.save_segments` drops
    it when the target actually changed. So a re-translation, a reviewer's edit
    and a memory hit each lift it by construction, and `lx run` — which
    re-extracts every time — keeps it, because a carryover is the same wording at
    the same position.
    """
    if not isinstance(waived, bool):
        raise UnusableTarget(
            f"`waived` is true or false, and this request sent "
            f"{type(waived).__name__}. A string or a null here is a client "
            f"building the payload wrongly — `null` would read as false and "
            f"*lift* a waiver, which is the opposite of the default.")
    if ids is None:
        ids = []
    if isinstance(ids, str) or not isinstance(ids, (list, tuple)):
        raise UnusableTarget(
            f"`ids` is a list of segment ids, and this request sent "
            f"{type(ids).__name__}. A bare string would be read one character at "
            f"a time and answer `applied: 0` while looking like it worked.")
    ids = [sid for sid in (str(sid).strip() for sid in ids) if sid]
    doc = load_doc(src, lang)
    by_id = {s["id"]: s for s in doc["segments"]}
    wanted = [sid for sid in ids if sid in by_id]
    unknown = [sid for sid in ids if sid not in by_id]
    if waived:
        blank = sorted(sid for sid in wanted if not (by_id[sid].get("target") or "").strip())
        if blank:
            raise UnusableTarget(
                f"{', '.join(blank)}: there is nothing here to waive — a waiver says you "
                f"read what `lx check` reports on this wording and stand by it, and these "
                f"segments have no wording yet. Translate them first: "
                f"`lx translate {src} --lang {lang} --ids {','.join(blank)}`.")
        # **And nothing to stand by if nothing fails.** Evaluated with the flag
        # forced off, so re-affirming a waiver already in force is not refused by
        # the very state it put there. Whole-request, like the blank refusal.
        #
        # It is not tidiness. A waiver on a passing segment reaches
        # `store.tm_record`, so the tracked memory grows a line claiming a
        # reviewer overrode a finding that never fired — and toggling one used to
        # append a full duplicate line per commit, six for one wording in the
        # measured run. Both found 2026-09-03 by the adversarial pass.
        glossary, dnt = load_glossary(cfg), load_dnt(cfg)
        clean = sorted(sid for sid in wanted if not any(
            i["severity"] == "error" for i in
            check_segment(dict(by_id[sid], waived=False), lang, cfg, glossary, dnt)))
        if clean:
            raise UnusableTarget(
                f"{', '.join(clean)}: `lx check` reports no error on these, so there is "
                f"nothing to stand by. A waiver answers a finding; it is not a mark of "
                f"approval, and one placed here would put a line in "
                f"`.lx/tm.{lang}.jsonl` saying a reviewer overruled a rule that never "
                f"fired. Use `lx hold` to say a segment is yours to finish.")
    applied, stale = save_waived(src, lang, {sid: waived for sid in wanted},
                                 expect={sid: by_id[sid].get("target") for sid in wanted})
    return applied, unknown, stale


def cmd_waive(args, cfg):
    ids = [s for s in args.ids.split(",") if s]
    applied, unknown, stale = do_waive(args.src, args.lang, cfg, ids, waived=not args.lift)
    verb = "un-waived" if args.lift else "waived"
    _out(f"{verb} {applied} segment(s)"
         + (f"; unknown ids ignored: {unknown}" if unknown else ""))
    if stale:
        _out(f"  {len(stale)} segment(s) were left alone because their wording changed "
             f"while this ran: {', '.join(stale)}. A waiver is about the words you read, "
             f"so read them again and repeat the command.")
    if not args.lift and applied:
        # Said on the way out rather than left to be discovered, because the one
        # thing a waiver does not buy is the thing a reviewer is most likely to
        # assume it buys.
        _out("  their errors are reported at warn now, not removed: `lx check` still "
             "prints them and still counts them under warnings. A waived wording is "
             "banked by `lx commit` and its memory line says it was waived, so the "
             "next document that takes it is told.")


def cmd_apply(args, cfg):
    raw = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    data = json.loads(raw)
    if isinstance(data, dict) and "segments" in data:
        data = data["segments"]
    incoming = ({d["id"]: d.get("text", d.get("target", "")) for d in data}
                if isinstance(data, list) else dict(data))
    # No `base`: a file on disk carries no token, so `lx apply` writes
    # unconditionally, exactly as it did. The lost-update check is opt-in per id
    # and the workbench is what opts in.
    applied, unknown, _stored, _conflicts, refused = do_apply(
        args.src, args.lang, cfg, incoming, args.origin,
        over_human=args.overwrite_human)
    _out(f"applied {applied} segment(s)" + (f"; unknown ids ignored: {unknown}" if unknown else ""))
    if refused:
        # Said out loud, for the reason `_run_translate` says it: `--origin`
        # takes free text, so `lx apply --origin llm:draft` reaches the guard,
        # and "applied 0 segment(s)" with exit 0 is a report nobody can act on.
        _out(f"{len(refused)} segment(s) were left alone because a person wrote them: "
             f"{', '.join(refused)}. Use `--origin agent` for your own words, or "
             f"pass --overwrite-human to replace theirs.")


# ── check ──────────────────────────────────────────────────────────────────

def do_check(src, lang, cfg, persist=True):
    doc = load_doc(src, lang)
    glossary, dnt = load_glossary(cfg), load_dnt(cfg)
    issues = []
    for seg in doc["segments"]:
        found = check_segment(seg, lang, cfg, glossary, dnt)
        if found:
            seg["issues"] = [f"{i['rule']}: {i['message']}" for i in found]
        else:
            seg.pop("issues", None)
        issues.extend(found)
    errors = [i for i in issues if i["severity"] == "error"]
    report = {
        "source": doc["source"], "lang": lang,
        "segments": len(doc["segments"]),
        "translated": sum(1 for s in doc["segments"] if s.get("target")),
        "errors": len(errors), "warnings": len(issues) - len(errors),
        "by_rule": dict(Counter(i["rule"] for i in issues)),
        "issues": issues,
    }
    if persist:
        # **One field, not one row.** This is the third writer none of the
        # seventeen divergences names, and it took two attempts to close.
        #
        # It began as `save_segments(src, lang, doc["segments"])`, which carried
        # a whole snapshot back: a target saved by the workbench, or banked by a
        # running job, between this function's read and its write was replaced by
        # the copy loaded here. The first fix added the compare-and-swap
        # `save_segments` already had — and an adversarial pass found that
        # insufficient the same day, because that swap compares the `target`
        # *column* while the statement writes the whole `body` blob, where
        # `origin`, `review` and `issues` all live. A hold placed while a check
        # was running was rolled back with `applied: 1` reported to the client
        # that placed it, and an `origin` rolled from `human` back to `tm` —
        # which is how a segment silently stops being covered by origin
        # precedence at all.
        #
        # `save_issues` touches the one key this function actually decides. The
        # `expect` on top is not what makes it safe — narrowness is — it only
        # keeps the stored issues from describing wording that has since moved.
        save_issues(src, lang,
                    {s["id"]: s.get("issues") for s in doc["segments"]},
                    expect={s["id"]: s.get("target") for s in doc["segments"]})
        dump_json(report_path(src, lang), report)
    return report, doc


def cmd_check(args, cfg):
    report, doc = do_check(args.src, args.lang, cfg)
    if args.json:
        _out(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _out(f"{report['source']} [{report['lang']}]  "
             f"{report['translated']}/{report['segments']} translated  "
             f"{report['errors']} error(s)  {report['warnings']} warning(s)")
        by_id = {s["id"]: s for s in doc["segments"]}
        for issue in report["issues"][: args.max]:
            _out(f"  {issue['severity']:5} {issue['seg']} {issue['rule']:12} {issue['message']}")
            _out(f"        src: {by_id[issue['seg']]['masked'][:88]}")
        if len(report["issues"]) > args.max:
            _out(f"  ... {len(report['issues']) - args.max} more (use --max or --json)")
    sys.exit(1 if report["errors"] else 0)


# ── render / commit / stats ────────────────────────────────────────────────

def do_blocks(src, lang, cfg, fallback=False):
    """The rendered document as an ordered block map. ``(blocks, missing)``.

    A block is ``{"id", "kind", "from", "text"}`` — :func:`skeleton.render_blocks`
    describes the record — and **the concatenation of their ``text`` is exactly
    what** :func:`do_render` **returns**, because `do_render` is written in terms
    of this. That property is what the reading view rests on: a client that joins
    the blocks it was handed gets the file `lx render` writes, and there is no
    second walk of the document for the two answers to drift apart in.

    **Blocks carry text, never integer spans**, and that is the decision rather
    than an implementation detail. Offsets are wrong twice over and neither error
    is visible on the LF-only ASCII fixtures a test would reach for first: a CRLF
    document shifts every one of them, because the terminator is re-imposed here
    rather than held in the nodes; and Python counts code points where JavaScript
    counts UTF-16 code units, so a name outside the BMP — routine in Chinese —
    desynchronizes the two silently. See `docs/decisions.md`, 2026-08-21.
    """
    doc = load_doc(src, lang)
    # From the document, never from the path: the skeleton is only readable by
    # the parser that wrote it, and a file renamed after extract would otherwise
    # be rebuilt by a different one.
    fmt = formats.for_doc(doc)
    if fmt.render_blocks is None:
        raise UnknownFormat(
            f"the {fmt.name} format renders documents but cannot report a block map, "
            f"so `lx blocks` and the workbench's reading view have nothing to show for "
            f"{src}. A format supplies `render_blocks` beside `render`; see "
            f"scriptorium/formats.py.")
    blocks, missing = fmt.render_blocks(
        doc, cfg, polish=lambda t: polish_rendered(t, lang, cfg),
        fallback=fallback, marker=fmt.marker)
    # Here rather than in write_document so every caller gets it: the file path,
    # `--out -`, and the workbench's render endpoint are all downstream of this.
    # Per block through `apply_terminator_parts` rather than per block through
    # `apply_terminator`, because the blanket `\r?\n` substitution does not
    # distribute over a concatenation — that function's docstring has the case.
    texts = apply_terminator_parts([b["text"] for b in blocks], doc.get("eol", "\n"))
    for block, text in zip(blocks, texts):
        block["text"] = text
    return blocks, missing


def do_render(src, lang, cfg, fallback=False):
    """The rendered target document as one string. ``(text, missing)``.

    **Through the registry's ``render``, not as the join of** :func:`do_blocks`.
    Written as that join it was shorter and it was wrong twice over, and both
    were found by an adversarial pass on 2026-08-21 rather than by a test:

    - ``Format.render`` became dead code — ``grep -rn "\\.render(" src/`` returned
      nothing — so a container format supplying its own ``render``, which is the
      case the slot exists for and the case ``AGENTS.md``'s "New format support"
      recipe describes, would have been silently ignored;
    - and the three tests asserting the blocks join back into what this returns
      became 56 assertions of ``join(blocks) == join(blocks)``, which cannot
      fail. Two paths through the registry is what makes them a measurement.

    The two agree by construction for both formats registered today, because
    :func:`skeleton.render` is itself the join of :func:`skeleton.render_blocks`
    — one walk of ``doc["nodes"]``, which is the invariant that matters. What the
    separation buys is the layer above it: the registry wiring, and a terminator
    re-imposed on the whole string here against one re-imposed part by part
    there, which are two genuinely different pieces of code.
    """
    doc = load_doc(src, lang)
    # From the document, never from the path: the skeleton is only readable by
    # the parser that wrote it, and a file renamed after extract would otherwise
    # be rebuilt by a different one.
    fmt = formats.for_doc(doc)
    text, missing = fmt.render(doc, cfg, polish=lambda t: polish_rendered(t, lang, cfg),
                               fallback=fallback, marker=fmt.marker)
    # Here rather than in write_document so every caller gets it: the file path,
    # `--out -`, and the workbench's render endpoint are all downstream of this.
    return apply_terminator(text, doc.get("eol", "\n")), missing


def do_sentences(texts, cfg):
    """Each string cut into sentences, in order. ``[[str, …], …]``, by index.

    The rule itself is :func:`sentences.split`; this is the seam every surface
    goes through, so the abbreviation list is read in one place and the batch
    shape cannot be re-invented per caller. The refusal lives here rather than at
    the endpoint for the reason `do_apply`'s empty-target refusal does: a rule the
    CLI can walk around is a rule that holds on one surface.

    The abbreviations are ``terms.abbreviations`` — the same list `lx terms`
    reads, never a second one — so a project that adds ``Ashcombe`` to it changes
    both answers together. That makes this function's output depend on the
    project's configuration, which the contract says out loud.
    """
    if not isinstance(texts, list):
        raise UnusableTarget("texts is an array of strings")
    for i, text in enumerate(texts):
        if not isinstance(text, str):
            # The value is deliberately not echoed. A reviewer's editor buffer is
            # what lands here, and this project does not repeat back a string it
            # refused — the rule invariant 6 holds for a credential, applied to a
            # field that has no reason to make an exception of itself.
            raise UnusableTarget(f"texts[{i}] is not a string")
    abbreviations = (cfg.get("terms") or {}).get("abbreviations", ())
    return [sentences.split(text, abbreviations) for text in texts]


def default_output(src, lang, cfg):
    pattern = cfg.get("output_pattern", "i18n/{lang}/{path}")
    return pattern.format(lang=lang, path=doc_label(src),
                          name=os.path.basename(src))


def cmd_render(args, cfg):
    text, missing = do_render(args.src, args.lang, cfg, args.fallback)
    if args.out == "-":
        write_document_to_stdout(text)
        return
    out = args.out or default_output(args.src, args.lang, cfg)
    write_document(out, text)
    _out(f"wrote {out}" + (f" ({missing} untranslated)" if missing else ""))


#: How much of a block's text one terminal line shows. Long enough that a
#: paragraph is recognizable, short enough that a book-length document is still
#: something a person can scroll.
_BLOCK_PREVIEW = 60


def _one_line(text):
    """``text`` on one line, with the characters that would break the line shown.

    Only the three that actually move a terminal cursor, spelled the way Python
    spells them, so the output is greppable and a hard break is visible as
    ``  \\n`` rather than as trailing space nobody can see.
    """
    return (text.replace("\\", "\\\\").replace("\r", "\\r")
                .replace("\n", "\\n").replace("\t", "\\t"))


def cmd_blocks(args, cfg):
    blocks, missing = do_blocks(args.src, args.lang, cfg, args.fallback)
    if args.json:
        _out(json.dumps({"blocks": blocks, "missing": missing},
                        ensure_ascii=False, indent=2))
        return
    for block in blocks:
        head = f"{block['id'] or '-':>6}  {(block['kind'] or 'skeleton'):9}"
        head += f"{(block['from'] or ''):7}"
        body = _one_line(block["text"])
        if len(body) > _BLOCK_PREVIEW:
            body = body[:_BLOCK_PREVIEW] + "…"
        _out(f"{head}{body}")
    _out(f"{len(blocks)} block(s), {missing} untranslated")


def cmd_sentences(args, cfg):
    doc = load_doc(args.src, args.lang)
    wanted = {i.strip() for i in args.ids.split(",") if i.strip()} if args.ids else None
    rows = []
    for seg in doc["segments"]:
        if wanted is not None and seg["id"] not in wanted:
            continue
        # The masked source under `--source`, and the stored target otherwise —
        # both of which still hold `⟦n⟧`, which is the string a reviewer's editor
        # holds and therefore the string the endpoint is handed. A rendered block's
        # text is `lx blocks`' answer, and running the rule over that is a
        # different question with a different command in front of it.
        text = seg["masked"] if args.source else (seg.get("target") or "")
        rows.append({"id": seg["id"], "sentences": do_sentences([text], cfg)[0]})
    if args.json:
        _out(json.dumps({"source": doc["source"], "lang": doc["lang"],
                         "segments": rows}, ensure_ascii=False, indent=2))
        return
    for row in rows:
        _out(f"{row['id']}  {len(row['sentences'])} sentence(s)")
        for n, sentence in enumerate(row["sentences"], 1):
            _out(f"    {n:>3}  {_one_line(sentence)}")


def do_commit(src, lang, cfg):
    """Bank this document's approved wordings. ``(committed, refused, held)``.

    The seam `lx commit` and `POST /api/commit` share. It did not exist until
    2026-09-01 — both surfaces called three `store` functions inline and were
    "equivalent by inspection rather than by construction", which the contract
    said out loud — and the moment anything but "has a target" decided what gets
    banked, that was two homes for one policy.

    **What may be banked is what `lx check` does not call an error**, per
    segment. `.lx/tm.*.jsonl` is a source of truth (invariant 9), it is tracked
    in git so damage travels with a pull, and `store.load_tm` keeps the *last*
    record per key — so a banked wording does not merely sit there being useless,
    it **shadows** the good one already banked under the same key. Measured
    2026-09-01: a correct wording banked, then a second document's broken one
    banked over it, and a third, brand-new document came back `reused 0,
    rejected 1` with the right sentence one line up in the file and unreachable.
    That is what makes "bank it and let the refusal at lookup time handle it"
    untenable: the refusal stops the bad wording rendering and does not bring the
    good one back.

    **`checks.check_segment` and not a rule of this function's own.** The
    placeholder gate `translate.accept` applies is an id *multiset*, and a
    swapped pair satisfies it: measured the same day, `這是⟦2⟧粗體⟦1⟧文字。`
    against `This is <b>bold</b> text.` is accepted, renders `</b>粗體<b>`, and
    reaches a second document intact. `checks.pair_problems` is what sees it, at
    the same `tags` rule and the same error severity — so the rule that already
    owns this question answers it here too, `checks_disabled` is honoured
    because there is one rule and not a copy of half of it, and a project that
    decides `numbers` is wrong for a novel gets the gate to agree by construction.
    *Lost:* the id multiset in `store.tm_records`, which is two lines and lets the
    swapped pair through. *Lost:* narrowing to the structural rules by name,
    which makes an enumeration the thing a reader trusts — the failure
    `docs/decisions.md` has recorded three times. *Lost:* refusing the whole
    commit when the document fails, which on a novel means banking a chapter
    waits for the book.

    **A held segment is not banked either**, and it is checked first, because
    "unhold it" is the more useful sentence when a segment is both. A hold is the
    reviewer's own declaration that this segment is theirs to finish, and
    `lx commit` takes a whole document — it is a batch act with no per-segment
    selection, so the hold is the only thing in it that can say "not this one".
    Nothing is lost: `tm_records` re-derives from the live segment every time, so
    an unhold at any later date makes the wording eligible for the very next
    commit. *Lost:* banking it anyway, which puts unfinished wording into the
    memory wearing no mark at all — `do_extract` deliberately does not carry a
    hold in with a hit, so the receiving document cannot know. *Lost:* a
    `--skip-held` flag, which answers "what does commit mean" once per call.

    **Nor is a wording that speaks a numbering this document has moved on from.**
    A segment carrying `target_slots` was stranded by a re-parse: it renders
    correctly, because the render reads the map its ids mean, and `lx check`
    reports it at *warn* — deliberately, since failing the build would block a
    book over a segment that comes out right. That severity is exactly why the
    error gate above does not catch it, and it is the population this whole
    package is about, so it needs saying separately: the memory is read by every
    document in this project under the numbering the project has **now**, and
    this wording does not speak it. Measured 2026-09-01 by the adversarial pass:
    banked, it shadowed a correct record under the same key and a third document
    came back `reused 0, rejected 1`. Re-word the segment and it banks.

    The three sets come back rather than being dropped, for the reason
    `store.save_targets` returns its refusals: a run reporting "+= 12 entries"
    while having declined four is a report nobody can act on.
    """
    doc = load_doc(src, lang)
    glossary, dnt = load_glossary(cfg), load_dnt(cfg)
    bankable, refused, held, stranded = [], [], [], []
    for seg in doc["segments"]:
        if not seg.get("target"):
            continue
        if is_held(seg):
            held.append(seg["id"])
        elif seg.get("target_slots"):
            stranded.append(seg["id"])
        elif any(i["severity"] == "error"
                 for i in check_segment(seg, lang, cfg, glossary, dnt)):
            refused.append(seg["id"])
        else:
            bankable.append(seg)
    # The document with its bankable segments, so `tm_records` still reads the
    # register off the document it was written for — the one place a key is built
    # from stored state, and the reason that function takes a document at all.
    committed = append_tm(lang, tm_records({**doc, "segments": bankable},
                                           load_tm(lang)))
    return committed, refused, held, stranded


def cmd_commit(args, cfg):
    committed, refused, held, stranded = do_commit(args.src, args.lang, cfg)
    _out(f"translation memory += {committed} entries")
    if refused:
        _out(f"  {len(refused)} segment(s) not banked because `lx check` reports an "
             f"error on them: {', '.join(refused)}. The memory keeps the last record "
             f"per key, so banking one would hide the wording already there. Fix "
             f"them and commit again.")
    if stranded:
        _out(f"  {len(stranded)} segment(s) not banked because their wording speaks a "
             f"numbering this document has moved on from: {', '.join(stranded)}. They "
             f"render as they were written and `lx check` reports them as "
             f"`numbering` warnings; re-word them against the source as it stands "
             f"and commit again.")
    if held:
        _out(f"  {len(held)} held segment(s) not banked: {', '.join(held)}. A hold "
             f"says the segment is yours to finish; `lx unhold {args.src} "
             f"--lang {args.lang} --ids {','.join(held)}` and commit again.")


def cmd_stats(args, cfg):
    """The progress bars, over `do_status`'s counts rather than a second set.

    Kept rather than folded into `lx status`, because removing a command is a
    break nobody asked for. Rewired because two commands counting one project's
    segments two ways is how they come to disagree, and these two already did:
    this one counted a target of three spaces as translated, where `store`
    derives the status from a stripped target and so does every other counter
    here. The bar moved on one document; the fix is that there is now one count.
    """
    project = do_status(cfg, lang=args.lang, detail=False)["projects"][0]
    if project["error"]:
        # **stderr and a failing exit code, both restored on purpose.**
        # `do_status` turns an unreadable project into a *field* because a
        # `--scan` has to list the rest of the library. This command has no such
        # field and exactly one project, so inheriting that swallow made it exit
        # 0 where it had exited 2 — and `.github/workflows/ci.yml`'s smoke step
        # is a bare `lx stats` at the end of a `set -euo pipefail` block, where
        # the exit code is the entire assertion. It went green on a database it
        # could not open. Measured 2026-08-19 by the pass that scored this
        # rewire against the command it replaced.
        #
        # stdout was the other half: `lx stats > coverage.txt` wrote the failure
        # into the report and left the terminal silent.
        print(f"lx: {project['error']}", file=sys.stderr)
        sys.exit(2)
    if not project["documents"]:
        _out("nothing tracked yet — run `lx extract`")
        return
    for row in project["documents"]:
        _out(f"{_bar(row['translated'], row['segments'])}  {row['source']} [{row['lang']}]")


# ── status ─────────────────────────────────────────────────────────────────

#: The version of `docs/contracts/status-json.md`, reported by `lx status --json`
#: as `contract_version`. Additive changes — a new key, a new optional flag, a
#: wider accepted value set — do not move it; a removal, a rename, a type change,
#: a meaning change or a narrowed value set does.
#:
#: **Not `web.server.CONTRACT_VERSION`.** Two surfaces, two consumers, two red
#: lines, and two numbers that move independently: a client reading one as the
#: other would watch its contract jump for a change to a surface it never calls.
STATUS_CONTRACT_VERSION = 1

#: What makes a directory a project, for `lx status --scan`. Either marker alone
#: is enough, and the *or* is the rule rather than a convenience. `lx init`
#: writes both. A cold `lx extract` writes only `.lx/`, because `store._connect`
#: creates it on the first write and `config.write_templates` never ran. A
#: project configured by hand and not yet extracted has only `lx.config.json`.
#: Requiring both would hide the second and third, which are exactly the two
#: states a library is found in — one book underway, one book set up last night.
#:
#: *Lost:* `.lx/state.db`, the marker that would mean "has work in it". A project
#: with no work is still a project a bookshelf has to show — showing it at 0% is
#: the point — and that marker names a file one layer deeper inside the storage
#: this contract exists to keep a consumer out of.
PROJECT_MARKERS = (STATE, "lx.config.json")

#: How far under `--scan`'s root a project may be found. Three, because a library
#: is `root/<shelf>/<book>` about as often as it is `root/<book>`, and the levels
#: cost nothing on a tree pruned at every project found. *Lost:* an unbounded
#: walk, which pointed at a home directory is a filesystem crawl nobody asked
#: for; and depth 1, which is all the acceptance criterion needed and which fails
#: the first person who groups books by shelf.
SCAN_DEPTH = 3


def project_markers(path):
    """Which of `PROJECT_MARKERS` `path` holds, in declaration order.

    Each is type-checked rather than merely present: a *file* named `.lx` and a
    *directory* named `lx.config.json` are neither of them a project, and on a
    case-folding filesystem a book called `LX.CONFIG.JSON` would otherwise be
    one. Returned rather than reduced to a boolean because the scan's own rule
    is the thing most likely to be argued with later, and a listing that shows
    which marker it matched can be argued with from evidence.
    """
    # Driven off `PROJECT_MARKERS` rather than repeating its two names, because
    # the constant's only other use is the sentence `lx status` prints when a
    # directory is not a project — so a third marker added there used to change
    # the help text and nothing else. One list, and the test that a marker of the
    # wrong type is not a project covers whatever is in it.
    checks = {STATE: os.path.isdir, "lx.config.json": os.path.isfile}
    return [name for name in PROJECT_MARKERS
            if checks[name](os.path.join(path, name))]


def find_projects(root, depth=SCAN_DEPTH):
    """Every project directory at or under `root`, sorted, each identity once.

    `root` itself is examined, at depth 0. Pointing `--scan` straight at a single
    project is the first thing anyone tries, and answering that with an empty
    list is a puzzle rather than a result.

    Three rules keep this from becoming a filesystem crawl. A directory
    identified as a project is **not descended into** — its `.lx/` and `config/`
    hold nothing this is looking for, and a project inside a project is not
    something this storage can express, since every document identity is
    `relpath` against one cwd. A child whose name begins with `.` is skipped,
    which covers `.git`, `.venv` and every dotted cache. And `depth` bounds the
    rest.

    Deduplicated on `os.path.realpath`, so a shelf of symlinks to one book
    reports it once, under the first path that reached it. Symlinks are followed:
    a library assembled out of them is an ordinary thing to build, and `depth` is
    what makes a cycle finite rather than a refusal to follow one.

    Not confined, and that is invariant 11 applied rather than skipped: `--scan`
    is a CLI argument, the invariant's named exception, a person typing a
    command. Nothing here opens a document — it stats two names per directory.
    The day a *request* carries a scan root, it goes through `cli.confined_path`
    at the surface that received it, and this function is not what changes.
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise FileNotFoundError(
            f"{root} is not a directory, so there is nothing under it to scan. "
            f"`lx status --scan` takes the directory your projects live in.")
    found, seen, frontier = [], set(), [(root, 0)]
    while frontier:
        path, level = frontier.pop()
        try:
            real = os.path.realpath(path)
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        if project_markers(path):
            found.append(path)
            # Pruned here, not before the marker test: the root of a library may
            # itself be a project, and its children still are not searched.
            continue
        if level >= depth:
            continue
        try:
            children = sorted(os.scandir(path), key=lambda e: e.name)
        except OSError:
            # A directory this process may not list is not an error worth ending
            # a scan for — a library under a home directory has several.
            continue
        # Reversed onto a LIFO frontier, so siblings are *visited* in sorted
        # order. Without this the first path to reach a target was the
        # alphabetically last one, which is what decides the `realpath` dedupe's
        # winner — three junctions `aaa`, `mmm`, `zzz` to one book reported it
        # under `zzz`, and adding a `zzzz` later silently changed the `path` of a
        # book nobody had touched. The contract calls `path` an identity.
        for entry in reversed(children):
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    frontier.append((entry.path, level + 1))
            except OSError:
                continue
    return sorted(found)


def _int(value):
    """An integer from a rebuildable artifact, or 0. `bool` is not an integer here."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _text(value):
    """A configured value this contract types `string | null`, or `None`.

    `lx.config.json` is hand-editable and nothing validates it on the way in, so
    a type the contract declares has to be kept on the way *out* — the argument
    `_int` makes for a check report, applied to the configuration. Measured
    2026-08-19: `tone` set to a JSON object was emitted as an object against a
    table saying `string | null`, and a consumer written to the table would have
    crashed on a file somebody typed by hand.

    **Not a masking function.** These fields are language tags and a register
    name, not places a credential is configured, and what keeps this surface out
    of invariant 6 is that it never reads the fields where one lives — not this.
    """
    return value if isinstance(value, str) else None


def _counts(segments):
    """`{segments, translated, pending, held, waived}` for one document.

    Counted from the **target text**, which is the same rule `store._segment`
    derives `status` from. Read off the text rather than off `status` so that the
    two cannot drift: the derived field is a convenience for a reader, and a
    counter that depends on it would inherit any future change to it silently.

    That makes the two spellings *equivalent today* and a mutation round says so
    — swapping this for `s.get("status") == "translated"` survives every test,
    because `_segment` recomputes the field on read from this very predicate.
    Recorded rather than chased: the mutant is equivalent, not uncaught, and the
    day `_segment` stops recomputing is the day it stops being.

    **Not** the rule `cli.do_check`'s report or `/api/state`'s `done` use. Both
    count any target at all; see `_check` and the contract's divergence (3).
    """
    translated = sum(1 for s in segments if (s.get("target") or "").strip())
    return {
        "segments": len(segments),
        "translated": translated,
        "pending": len(segments) - translated,
        "held": sum(1 for s in segments if is_held(s)),
        # Counted off the live segments beside `held`, and **not** read out of
        # the persisted report the way `errors` and `warnings` are. A waiver is
        # state, not a finding: it is true the moment `lx waive` returns, where a
        # report is only as current as the last `lx check`. Reading it from the
        # report would also inherit `_check`'s staleness hole — the two integers
        # `stale` compares do not move when a waiver is placed — so a consumer
        # would be told `waived: 0` on a document that had just waived four.
        "waived": sum(1 for s in segments if is_waived(s)),
    }


def _as_report(segments):
    """`do_check`'s own translated count, for comparing a report against itself.

    Deliberately **not** `_counts`' predicate: this one has no `.strip()`,
    because `cli.do_check` has none either and a staleness test between two
    numbers counted two ways is a test that can never pass. See `_check`.
    """
    return sum(1 for s in segments if s.get("target"))


def _check(src, lang, counts, as_report):
    """The last `lx check`'s error and warning counts for one document, or `None`.

    Read from `.lx/reports/`, which is a **rebuildable artifact** and not state
    (invariant 9), so every value here is a projection of a file that may be
    older than the document. `None` means no report exists — a document nobody
    has checked, which a consumer must not draw as a clean one.

    `stale` is a one-way signal and the contract says so: `true` means the report
    definitely no longer describes this document, because the segment or
    translated count has moved since it was written. `false` means only that
    those two numbers still agree — a sentence rewritten in place moves neither.
    A timestamp would settle it and there is none anywhere in the state; see
    `docs/contracts/status-json.md`, *Deliberately not in the contract*.

    Never raises. A corrupt or unreadable report is a missing one, because the
    alternative is a project that cannot be listed on account of a file that can
    be regenerated by running one command.
    """
    try:
        report = load_json(report_path(src, lang), {})
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict) or not report:
        return None
    return {
        "errors": _int(report.get("errors")),
        "warnings": _int(report.get("warnings")),
        # **Compared in the report's own arithmetic, not in this surface's.**
        # `do_check` writes `translated` with a different predicate —
        # `s.get("target")`, no strip, `cli.do_check` — and comparing it against
        # the stripped count above made `stale` permanently true for any document
        # holding a whitespace-only target: true on a report written one second
        # earlier, and no amount of re-running `lx check` could clear it, because
        # the two numbers never agree on such a row by construction. Staleness
        # asks whether the document moved since the report was written, so both
        # sides of it have to count the same way. Measured 2026-08-19.
        "stale": (report.get("segments") != counts["segments"]
                  or report.get("translated") != as_report),
    }


def _rollup(rows):
    """The counters shared by a language rollup and a project total.

    `checked` is here because summing `errors` over documents is misleading
    without it: a document nobody has checked contributes zero and reads exactly
    like a clean one. The pair — "3 errors across 5 of 7 checked" — is the
    smallest honest statement of quality this surface can make.
    """
    out = {"documents": len(rows), "checked": 0, "segments": 0, "translated": 0,
           "pending": 0, "held": 0, "waived": 0, "errors": 0, "warnings": 0}
    for row in rows:
        for key in ("segments", "translated", "pending", "held", "waived"):
            out[key] += row[key]
        if row["check"] is not None:
            out["checked"] += 1
            out["errors"] += row["check"]["errors"]
            out["warnings"] += row["check"]["warnings"]
    return out


def _output(src, lang, cfg):
    """Where `lx render` would write this document, or `None`.

    The one thing that closes the loop for the bookshelf, which is also the
    reader: it gets a document's `source` and, without this, no supported way to
    find the translated text. `output_pattern` is per-project configuration, so
    hard-coding the default breaks silently on any project that changed it,
    reading `lx.config.json` is the storage coupling the red line exists to
    stop, and shelling out to `lx render` violates "this contract and nothing
    else". All three were the consumer's only options until 2026-08-19.

    `cli.default_output` rather than a second copy of the pattern: `lx render`
    writes exactly where this says it will, or the two would disagree about a
    path the consumer then opens.

    `None` when the pattern cannot be formatted — an unknown `{placeholder}` is a
    `KeyError`, `{0}` an `IndexError`, an unbalanced brace a `ValueError`. A
    project whose pattern is mistyped still reports its counts; taking the whole
    listing down over a string nobody has rendered with yet is the wrong trade.
    """
    try:
        return default_output(src, lang, cfg)
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        return None


def _document(doc, cfg, detail=True):
    # Refused rather than passed through, and refused *here* so that the sentence
    # lands in the project's `error` and the rest of the library still lists.
    # `store._meta` tolerates a row whose meta carries no `source` — it guards
    # its own normalization with `if doc.get("source")` — and everything
    # downstream of this point treats it as a path: `report_path(None, lang)`
    # reached `os.path.relpath(None)` and ended the whole command in a
    # `TypeError` that `main` does not catch, with no report produced at all.
    # Found by the security-tier pass on 2026-08-19.
    if not isinstance(doc.get("source"), str):
        raise ValueError(
            f"a stored document row carries no source path, so this build cannot name "
            f"it. The state is rebuildable: delete {db_path()} and re-run `lx extract`.")
    counts = _counts(doc["segments"])
    return {
        "source": doc.get("source"),
        "lang": doc.get("lang"),
        "format": doc.get("format"),
        "tone": doc.get("tone"),
        "state_version": doc.get("state_version"),
        "output": _output(doc["source"], doc.get("lang"), cfg),
        "segments": counts["segments"],
        "translated": counts["translated"],
        "pending": counts["pending"],
        "held": counts["held"],
        "waived": counts["waived"],
        "check": (_check(doc.get("source"), doc.get("lang"), counts,
                         _as_report(doc["segments"])) if detail else None),
    }


def _empty_project(path):
    return {
        "path": path,
        "name": os.path.basename(path.rstrip(os.sep + (os.altsep or ""))) or path,
        "markers": project_markers(path),
        "source_lang": None,
        "targets": [],
        "tone": None,
        "documents": [],
        "untracked": [],
        "languages": [],
        "totals": _rollup([]),
        "error": None,
    }


def _project(path, cfg=None, lang=None, detail=True):
    """One project's entry. **Never raises.**

    A project this build cannot read reports `error` and empty counts rather than
    ending the command, which is the rule `/api/state` already follows for a
    malformed routing stage: one bad entry must not take the listing down. Under
    `--scan` that is the difference between a library that lists and a library
    that does not, and the offending project is usually the one the person wants
    to be told about.

    Broad on purpose. The reachable failures are a `state.db` written by a newer
    schema (`StateVersionError`), a `lx.config.json` that is not JSON
    (`ValueError`), a database that is not a database (`sqlite3.DatabaseError`),
    and a directory that has become unreadable between the scan and the read
    (`OSError`) — four unrelated hierarchies, and the *next* one is what the
    narrow spelling would miss. `KeyboardInterrupt` and `SystemExit` are not
    `Exception` and still end the command.
    """
    entry = _empty_project(path)
    try:
        # Reloaded per project under `--scan`, never inherited: `main` loads the
        # configuration of the directory `lx` was invoked in, and reporting one
        # project's `targets` against another's config is how a listing comes to
        # describe books nobody configured that way.
        cfg = load_config() if cfg is None else cfg
        entry["source_lang"] = _text(cfg.get("source_lang"))
        entry["tone"] = _text(cfg.get("tone"))
        # The container is type-checked before its elements, and that is the
        # half that bites: `list("zh-TW")` is `['z', 'h', '-', 'T', 'W']`, so a
        # hand-edited `"targets": "zh-TW"` — the likeliest typo there is —
        # reported five target languages named after its own letters.
        configured = cfg.get("targets")
        entry["targets"] = ([t for t in configured if isinstance(t, str)]
                            if isinstance(configured, list) else [])
        # **Read once, unfiltered, and filtered here.** `do_untracked` subtracts
        # what is already tracked, so handing it a `--lang`-filtered list would
        # make it offer a document that is tracked in another language — the
        # subtraction is over (identity, language) pairs and a missing pair is a
        # false offer. `tracked(lang)` filters in Python anyway, so this is one
        # database read either way.
        stored = tracked()
        entry["documents"] = [_document(d, cfg, detail) for d in stored
                              if not lang or d.get("lang") == lang]
        if detail:
            # `cli.do_untracked` and nothing of its own: this key, `lx untracked`
            # and `/api/state`'s `untracked` are required to spell one word and
            # mean one thing, which is what the rename of 2026-08-14 was for.
            rows, _collisions = do_untracked(cfg, stored)
            entry["untracked"] = [r for r in rows if not lang or r.get("lang") == lang]
        by_lang = {}
        for row in entry["documents"]:
            by_lang.setdefault(row["lang"], []).append(row)
        # `lang` first in the object, because this is the key that says which
        # rollup a reader is looking at and a listing is read top to bottom.
        entry["languages"] = [
            dict({"lang": name}, **_rollup(rows))
            for name, rows in sorted(by_lang.items(), key=lambda kv: str(kv[0]))]
        entry["totals"] = _rollup(entry["documents"])
    except Exception as e:  # noqa: BLE001 — see the docstring
        # **Rebuilt, not annotated.** The contract says an entry carrying an
        # `error` has zero counts and empty lists, and a failure part-way through
        # the projection above would otherwise leave half of one behind — a
        # document list that stops wherever the exception happened, with totals
        # that never ran. Returning a fresh entry is what makes that sentence
        # true whichever line raised.
        failed = _empty_project(path)
        failed["error"] = str(e) or e.__class__.__name__
        return failed
    return entry


def do_status(cfg, scan=None, depth=SCAN_DEPTH, lang=None, detail=True):
    """The project-status projection. `docs/contracts/status-json.md` is the contract.

    Without `scan`, `projects` holds exactly one entry — the working directory,
    reported whether or not it carries a marker, because that is where the person
    is standing. With `scan`, it holds one entry per project found under the root,
    sorted by path, and an empty list is a real answer.

    One shape either way. A consumer reads `projects` and never branches on which
    flag produced it. *Lost:* a bare object for the single-project case, which
    reads better in a terminal and forces every consumer to write the branch.

    ``detail=False`` skips the two projections beyond the counts — the
    per-document read of `.lx/reports/` and the `sources` glob behind
    `untracked` — and is **not part of the contract**: `cmd_status` never passes
    it and `lx status --json` always carries both. It exists for `lx stats`,
    which prints neither. Measured 2026-08-19 on 2000 documents: `store.tracked`
    alone is 117 ms and the report reads take that to 741 ms, so the incumbent
    command would have paid six times its own read for output it discards, plus
    a filesystem walk per project. What it does *not* do is compute the counts
    twice — that is the whole reason `lx stats` was rewired through here, and it
    stays one computation.

    The cost of the flag, stated because it is a footgun: with it off, `check`
    is `None` for every document and `untracked` is `[]`, which are the same
    shapes as "nobody has checked this" and "nothing new matches `sources`".
    Only a caller that reads neither may pass it.

    **This changes the working directory and puts it back.** Every path in this
    project is relative to `os.getcwd()` — `store.db_path`, `store.doc_id` and
    `config.load_config` all are — so reading a project means standing in it.
    The restore is in a `finally`, and the directory it restores to is captured
    once, before the first move. *Lost:* threading a root through `store.py`,
    which is the right shape and is a shared-seam edit this package is explicitly
    scoped out of; it is recorded as the follow-up in `docs/decisions.md`.
    """
    here = os.getcwd()
    roots = [here] if scan is None else find_projects(scan, depth)
    projects = []
    try:
        for path in roots:
            try:
                os.chdir(path)
            except OSError as e:
                entry = _empty_project(path)
                entry["error"] = str(e)
                projects.append(entry)
                continue
            # `cfg` only for the working directory: it is the one whose config
            # `main` already loaded, and the one whose `--config` the person
            # meant. A scanned project loads its own.
            projects.append(_project(path, cfg if scan is None else None, lang, detail))
    finally:
        os.chdir(here)
    return {
        "contract_version": STATUS_CONTRACT_VERSION,
        "version": __version__,
        "scanned": None if scan is None else scan,
        "lang": lang,
        "projects": projects,
    }


def _bar(done, total):
    pct = done * 100 // max(total, 1)
    return f"{pct:3d}% [{'#' * (pct // 5):<20}] {done}/{total}"


def _print_project(project, indent=""):
    for row in project["documents"]:
        line = f"{indent}{_bar(row['translated'], row['segments'])}  {row['source']} [{row['lang']}]"
        check = row["check"]
        if check:
            stale = ", stale" if check["stale"] else ""
            line += f"  {check['errors']} error(s), {check['warnings']} warning(s){stale}"
        else:
            line += "  unchecked"
        if row["held"]:
            line += f", {row['held']} held"
        # Printed beside the counts and not behind a flag, because the pair is
        # the reading: `0 error(s)` on a document with a waiver is a person's
        # judgement rather than a clean sweep, and this is the surface a
        # maintainer actually looks at before saying the book is done. The JSON
        # gained the counter and the terminal did not, which was the same half-fix
        # the status contract's own `checked` counter exists to stop.
        if row["waived"]:
            line += f", {row['waived']} waived"
        _out(line)


def cmd_status(args, cfg):
    status = do_status(cfg, scan=args.scan, depth=args.depth, lang=args.lang)
    if args.json:
        _out(json.dumps(status, ensure_ascii=False, indent=2))
        return
    for project in status["projects"]:
        totals = project["totals"]
        head = f"{project['name']}  {project['path']}"
        if project["error"]:
            _out(f"{head}  — cannot be read: {project['error']}")
            continue
        _out(head)
        _print_project(project, indent="  ")
        if not project["documents"]:
            # Which of the two nothings this is, because they need different
            # answers: a directory nobody has set up, and a project set up and
            # not yet extracted.
            _out("  nothing tracked yet — run `lx extract`" if project["markers"]
                 else "  not a project — no .lx/ and no lx.config.json")
            continue
        _out(f"  {totals['segments']} segments, {totals['translated']} translated, "
             f"{totals['pending']} pending, {totals['held']} held, "
             f"{totals['waived']} waived — "
             f"{totals['errors']} error(s), {totals['warnings']} warning(s) "
             f"across {totals['checked']} of {totals['documents']} checked")
    if status["scanned"] is not None and not status["projects"]:
        _out(f"no project under {status['scanned']} — a project is a directory holding "
             f"{' or '.join(PROJECT_MARKERS)}")


def cmd_init(args, cfg):
    created = write_templates()
    _out("initialized" + (f": {', '.join(created)}" if created else " (already set up)"))


# ── untracked ──────────────────────────────────────────────────────────────

def _fold(identity):
    """A document identity as the filesystem would compare it.

    ``os.path.normcase`` lowercases on Windows and is the **identity function on
    POSIX**, so this is the platform's own answer to "are these the same name"
    rather than a rule of ours — which is exactly what stops it merging two
    genuinely distinct documents where the filesystem keeps them apart, the
    objection that ruled out case-folding :func:`store.doc_id` itself. Contract
    divergence (19): the identity is case-sensitive, NTFS is not, so
    `lx extract docs/guide.md` against an on-disk `docs/Guide.md` tracked the file
    and the listing went on offering it.

    *Lost:* ``os.path.normcase(os.path.realpath(p))``, which additionally folds
    8.3 short names, junctions and symlinks. It was written first and measured
    out: `realpath` is a syscall per path, and building the tracked side of the
    comparison cost **463 ms on 2000 documents — 4.7x the `tracked()` read it
    rides beside** — on the endpoint a client must call before it can draw
    anything, for a library scale this project treats as small. The same sweep
    here is **27 ms**, most of it the `doc_id` that was already being computed,
    and `do_untracked` on that shape went from **672 ms to 212 ms**. It also needs
    no filesystem at all, so a tracked document whose file is gone still compares
    rather than resolving to a literal that folds differently. What it does not
    fold is a symlink or an 8.3 spelling reaching one file two ways; that is the
    identity's own defect and waits with the structural identity the contract's
    *Reserved* section schedules.

    The measurement is the point and not the number: the first version bought
    coverage nobody had asked for, on the axis that had not been decided, at four
    times the cost of the read beside it.

    Folded on the **subtraction only**, never on `labels`: two spellings that the
    filesystem calls one file are one document, not a collision, and reporting
    them as one would be inventing a defect.
    """
    return os.path.normcase(identity)


def _extractable(path, cfg):
    """Could `lx extract` read this path at all?

    Two axes, and deliberately only the two both surfaces agree on: it has to be
    a file, and the format registry has to know its extension. A `sources` value
    is a glob and nothing else — `book/**/*` matches the cover image and the
    chapter subdirectory alongside the chapters — and offering those as work to
    start is offering a refusal. Measured 2026-08-14: `formats.name_for_path`
    raises for both, so `lx extract` exits 2 and `POST /api/extract` answers 400,
    with "has no format this project knows how to read".

    The third axis contract divergence (20) names — a path outside the project
    root — is **not** filtered. `confined_path` refuses it at the endpoint, but a
    CLI argument is invariant 11's named exception and
    `lx extract ../shelf/book.md` succeeds, measured the same day. Filtering it
    would take a row out of the list that the product's own primary surface can
    act on (invariant 8). It stays until `roots` makes an outside path a
    first-class thing rather than a colliding identity.
    """
    if not os.path.isfile(path):
        return False
    try:
        formats.name_for_path(path, cfg)
    except UnknownFormat:
        return False
    return True


def _add_label(labels, identity, label):
    """Record a spelling this identity was reached through, once."""
    seen = labels.setdefault(identity, [])
    if label not in seen:
        seen.append(label)


def do_untracked(cfg, docs=None):
    """``([{source, lang}], [{paths, offered}])`` — what is untracked, and what collided.

    One entry per configured target language, because a file extracted into
    zh-TW and not into ja-JP is untracked in ja-JP and tracked in zh-TW.

    ``docs`` is :func:`store.tracked`'s result, a parameter so that a caller
    already holding it does not pay for a second one. It is the whole of
    `/api/state`'s document list, and that endpoint used to load every segment
    of every document in the project twice per request to draw one page.

    **The comparison is** :func:`store.doc_id`, **this project's identity
    function, rather than a normalization of this function's own.** Two things
    follow, and both are the reason to reuse it. It flattens the separator, which
    is what the comparison needed: a state row written `docs\\guide.md` and a glob
    hit spelled `docs/guide.md` are one file, and comparing the two strings meant
    the subtraction never fired at all on a platform whose separator is not `/`
    — measured 2026-08-13, and the workbench went on offering to extract a
    document it was already showing. And it is what `.lx/state.db` keys a
    document row on, so two paths it maps together really are one document here:
    a listing that separated them would be offering an extract that overwrites
    the other one's state. *Lost:* a second normalization rule owned by this
    function, which is how one matcher in this repository became three copies
    before anyone noticed.

    Nothing here is confined and nothing here opens a file (invariant 11): these
    paths are emitted for a person or a client to choose from, and the endpoint
    that acts on one — `/api/extract`, through :func:`confined_path` — is where a
    path out of a hand-edited `sources` is refused. Recorded rather than assumed,
    because `sources` feeds a glob directly and is not in
    `config.PATH_VALUED_KEYS`.

    **The second return value is what a suppressed file costs.** `doc_id`
    flattens every character outside ``A-Za-z0-9._-``, so `docs/guide.md` and a
    root-level `docs_guide.md` are one identity — and so are `books/第一章.md`
    and `books/第二章.md`, which is a whole Chinese-titled library collapsing to
    one row in the use case this project exists for. The suppression is faithful
    to storage and must stay: extracting the second overwrites the first's state.
    What was wrong is that neither surface said which path it had collapsed. Each
    entry is ``{"paths": [...], "offered": <path or null>}`` — every spelling that
    maps to one identity, and which of them the list carries. ``offered`` is null
    when a tracked document already holds the identity, which is the case that
    produced no entry at all and was therefore completely invisible. See
    ``docs/contracts/workbench-http.md`` (18).
    """
    if docs is None:
        docs = tracked()
    # Folded, so a candidate reached under another case is subtracted by the
    # document that already covers it — and so two spellings of one file inside
    # one call are offered once. See `_fold`.
    seen = {(_fold(doc_id(d["source"])), d["lang"]) for d in docs}
    # Every distinct spelling an identity was reached through, tracked documents
    # included — one document tracked in two languages is one spelling, which is
    # why this is not a plain append. Unfolded on purpose: see `_fold`.
    labels = {}
    for d in docs:
        _add_label(labels, doc_id(d["source"]), d["source"])
    out, offered = [], {}
    for pattern in cfg.get("sources") or []:
        for path in sorted(glob.glob(pattern, recursive=True)):
            try:
                identity, rel = doc_id(path), doc_label(path)
            except ValueError as e:
                # `os.path.relpath` raises across volumes on Windows, and both
                # calls above make one. A pattern naming another drive or a UNC
                # share is an ordinary thing to write on a machine whose library
                # is not on C:, and it used to end this command in a traceback
                # and exit 1 — where every other refusal in this CLI is one
                # sentence and exit 2, and where the workbench turns this into a
                # 400 quoting a CPython internal.
                raise ConfigError(
                    f"the sources pattern {pattern!r} matched {path!r}, which is not on the "
                    f"same volume as the project directory ({e}). Every document's identity "
                    f"here is its path relative to that directory, so a pattern has to stay "
                    f"under it — move the files in, or start `lx` where they already are."
                ) from None
            # Before the identity is recorded: a directory or a cover image is
            # not a document this project could read under any spelling, so it is
            # neither work nor a collision worth reporting.
            if not _extractable(path, cfg):
                continue
            _add_label(labels, identity, rel)
            folded = _fold(identity)
            for lang in cfg.get("targets") or []:
                key = (folded, lang)
                if key in seen:
                    continue
                # Recorded as it is emitted, so two overlapping patterns propose
                # one file once. The identity is the one the state row uses, so a
                # repeat here would be two offers of a row the database can only
                # hold once — the same subtraction, applied to this call's own
                # output.
                seen.add(key)
                out.append({"source": rel, "lang": lang})
                offered.setdefault(identity, rel)
    collisions = sorted(
        ({"paths": sorted(paths), "offered": offered.get(identity)}
         for identity, paths in labels.items() if len(paths) > 1),
        key=lambda c: c["paths"])
    return out, collisions


def _report_collisions(collisions):
    """Name the files one identity swallowed, or print nothing at all.

    Printed after the list rather than inside it, and printed even when the list
    is empty: "nothing new matches sources" is exactly the wrong answer to give
    someone whose library is entirely Chinese-titled and has collapsed to one row.
    """
    if not collisions:
        return
    _out(f"{len(collisions)} identity(ies) are shared by more than one path, and this "
         f"project can track one document per identity:")
    for c in collisions:
        offered = (f"offering {c['offered']}" if c["offered"]
                   else "none offered; a tracked document holds this identity")
        _out(f"  {' = '.join(c['paths'])} — {offered}")
    _out("  rename one of each set if both are meant to be translated — `.lx/state.db` "
         "keys a document on that identity, so extracting the second would overwrite "
         "the first's state")


def cmd_untracked(args, cfg):
    rows, collisions = do_untracked(cfg)
    if args.json:
        # An object carrying the array plus the two configuration values that
        # decided it, which is the shape `lx todo`, `lx terms` and `lx check
        # --json` all emit — so an empty list explains itself to a machine
        # consumer as well as to a person. The array's own name follows
        # `lx terms`, the one of the three that names its array for the command
        # (`lx todo`'s is `segments` and `lx check`'s is `issues`, which name the
        # payload): here the command, `/api/state`'s key after the rename and
        # HANDOFF-203's field have to spell one word, and that word is this
        # command's.
        _out(json.dumps({"sources": list(cfg.get("sources") or []),
                         "targets": list(cfg.get("targets") or []),
                         "untracked": rows, "collisions": collisions},
                        ensure_ascii=False, indent=2))
        return
    patterns, targets = cfg.get("sources") or [], cfg.get("targets") or []
    # Both emptiness cases produce an empty list and neither is "everything is
    # tracked", so each says which key is empty rather than reporting nothing to
    # do. `targets` first: with no target language there is no pair to be
    # untracked in, whatever the globs match.
    if not targets:
        _out("no target language is configured, so nothing can be untracked in one — "
             "`lx config set targets zh-TW`")
        return
    if not patterns:
        _out("`sources` is empty, so nothing is looked for — "
             '`lx config set sources "docs/**/*.md"`')
        return
    if not rows:
        _out(f"nothing new matches sources ({', '.join(patterns)}) "
             f"for {', '.join(targets)}")
        # After the sentence, not instead of it: an identity collision is the one
        # way that sentence can be true and misleading at the same time.
        _report_collisions(collisions)
        return
    _out(f"{len(rows)} untracked (source, language) pair(s)")
    # Floored, because a negative slice counts from the tail while the
    # arithmetic below counts from the head: `--max -1` printed 29 rows and then
    # claimed 31 more. `cmd_check`'s block, which this one follows, has the same
    # defect and is left alone here — it is a different command's line to change.
    shown = max(0, args.max)
    for row in rows[:shown]:
        _out(f"  {row['source']} [{row['lang']}]")
    if len(rows) > shown:
        # `lx check`'s sentence verbatim, because this is the same promise: the
        # human display is truncated and the whole list is one flag away. The
        # wire is not capped and neither is `--json`.
        _out(f"  ... {len(rows) - shown} more (use --max or --json)")
    _report_collisions(collisions)
    _out("`lx extract <src> --lang <lang>` to track one")


# ── configuration ──────────────────────────────────────────────────────────

#: An environment variable's name: what `api_key_env` holds, and the only thing
#: it may hold. `fullmatch`, and the length bounded inside the pattern rather
#: than after it, because `$` matches *before* a trailing newline — a pasted
#: `"OPENAI_API_KEY\n"` satisfies `^…$` and a trailing newline is exactly what a
#: clipboard carries. `_LANG_RE` above avoids the same trap by ending in `\Z`.
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")

#: A credential of this length is essentially never upper-case, and an
#: environment variable name of this length essentially always is. See
#: `_field_api_key_env` for why shape alone does not decide it.
_ENV_LONG = 20

#: Below this, a value equal to some variable's content is a coincidence
#: (`OS=Windows_NT`, `SessionName=Console`) rather than a pasted key.
_ENV_CONTENT_FLOOR = 8

_NOT_A_NAME = "<not an environment variable name — see `lx config set --help`>"
_HIDDEN_HEADER = "<hidden — a header value is sent to the backend verbatim>"

_ENV_SHAPE_ADVICE = (
    "{path} takes the NAME of an environment variable, never a key — and the "
    "value is not repeated here, in case it is one. Names are letters, digits "
    "and underscores, not starting with a digit. Export the key under a name, "
    "then give the name:\n"
    "  export OPENAI_API_KEY=…    (PowerShell: $env:OPENAI_API_KEY = '…')\n"
    "  lx config set {path} OPENAI_API_KEY"
)

_URL_ADVICE = (
    "{path} is the base URL of an HTTP endpoint, and the value is not repeated "
    "here — this field sits right above api_key_env and is one of the two a "
    'mispasted key lands in. A local runtime looks like '
    '"http://localhost:11434/v1"; a hosted one like "https://api.openai.com/v1".'
)

_ENV_LOOKS_LIKE_KEY_ADVICE = (
    "{path} was given something long and lower-case, which is the shape of a key "
    "rather than of a variable name — it is not repeated here in case that is "
    "what it is. Export it under a name and give the name instead:\n"
    "  lx config set {path} OPENAI_API_KEY\n"
    "If that really is your variable's name, set it in this environment first: a "
    "name that is already exported is always accepted."
)


def _as_text(path, value, what):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} is {what}, as text — got {value!r}.")
    return value.strip()


def _as_number(path, value, what, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ConfigError(f"{path} is {what} — got {value!r}.")
    try:
        number = float(value)
    except ValueError:
        raise ConfigError(f"{path} is {what} — got {value!r}.") from None
    # `nan` and `inf` are what `float()` accepts and no window rejects: every
    # comparison against a `nan` is False, so it passes `low` and `high` both,
    # and `int(nan)` then raises the interpreter's own ValueError — which is not
    # a `ConfigError`, so it reached the user as a traceback and exit 1 where
    # every other refused value gets one sentence and exit 2. `1e400` is `inf`
    # by the same door.
    if not math.isfinite(number):
        raise ConfigError(f"{path} is {what} — got {value!r}.")
    if (low is not None and number < low) or (high is not None and number > high):
        window = f"between {low} and {high}" if high is not None else f"{low} or more"
        raise ConfigError(f"{path} is {what}, {window} — got {value!r}.")
    return int(number) if number == int(number) else number


def _as_count(path, value, what, low=1):
    number = _as_number(path, value, what, low=low)
    if number != int(number):
        raise ConfigError(f"{path} is {what}, a whole number — got {value!r}.")
    return int(number)


def _as_list(path, raw):
    """A list from one command-line word: JSON, or the friendlier comma form."""
    if isinstance(raw, list):
        return raw
    text = raw.strip()
    if text.startswith("["):
        try:
            decoded = json.loads(text)
        except ValueError as e:
            raise ConfigError(f"{path} is a list, and that is not valid JSON ({e}).") from None
        if not isinstance(decoded, list):
            raise ConfigError(f"{path} is a list.")
        return decoded
    return [part.strip() for part in text.split(",") if part.strip()]


def _as_block(path, raw):
    if isinstance(raw, dict):
        return raw
    try:
        decoded = json.loads(raw)
    except ValueError as e:
        raise ConfigError(
            f"{path} is a block, so its value is a JSON object — "
            f"""`lx config set {path} '{{"key": "value"}}'` ({e}).""") from None
    if not isinstance(decoded, dict):
        raise ConfigError(f"{path} is a block, so its value is a JSON object.")
    return decoded


def _field_kind(cfg, path, value):
    from .providers import KINDS
    kind = _as_text(path, value, "a backend kind")
    if kind not in KINDS:
        raise ConfigError(
            f"{path} = {kind!r} is not a backend this build has. "
            f"Accepted: {', '.join(sorted(KINDS))}.")
    return kind


def _field_base_url(cfg, path, value):
    """Where the document under translation is sent. Refuses what cannot be that.

    **No refusal here echoes the value.** This field sits directly above
    `api_key_env` in every provider block, so it is one of the two a mispasted
    key lands in — and a refusal that names the accepted shape must not also
    repeat what was rejected. Measured: the not-a-URL branch interpolated it, so
    `lx config set providers.openai.base_url sk-ant-…` printed the key to stderr,
    into the scrollback, and into any CI log that captured it.

    **A query string is refused as well as userinfo.** A credential reaches a URL
    two ways — `https://u:p@host/v1` and `https://host/v1?key=…` — and both put
    it into a file `lx init` scaffolds into a repository. Refusing *every* query
    rather than a guessed-at list of parameter names is invariant 4: a query in a
    `base_url` is unusual, the rule is decidable without judgement, and the
    escape for a genuine one is hand-editing the file, exactly as it is for a
    header.
    """
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(_URL_ADVICE.format(path=path))
    url = value.strip()
    try:
        parsed = urllib.parse.urlsplit(url)
        carries = bool(parsed.username or parsed.password or parsed.query)
    except ValueError:
        raise ConfigError(_URL_ADVICE.format(path=path)) from None
    if carries:
        raise ConfigError(
            f"{path} carries a username, a password or a query string, and any of the "
            f"three is how a key ends up in a file meant to be committed (invariant 6) "
            f"— the value is not repeated here in case that is what it is. Give the "
            f"endpoint on its own, and name an environment variable in "
            f"{path.rsplit('.', 1)[0]}.api_key_env.")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(_URL_ADVICE.format(path=path))
    return url


def _field_api_key_env(cfg, path, value):
    """The NAME of an environment variable, and never a key.

    Four rules after the empty case, first decision wins, and **not one of them
    ever echoes the value** — the single thing a field that should hold a name
    is likely to hold instead is the credential itself, and a refusal that
    quotes it has published it to the terminal, to the scrollback, and to
    whatever the output was piped into.

    *Shape alone is a sieve.* Refusing everything but `[A-Za-z_][A-Za-z0-9_]*`
    turns away every hyphenated format — `sk-…`, `sk-ant-…`, `xai-…` — and
    admits `hf_…`, `ghp_…`, `github_pat_…`, `gsk_…`, `r8_…` and every hex or
    base62 token that happens to start with a letter. So shape is the first
    rule, not the only one.

    *What catches those is length and case.* An environment variable name is
    either short or upper-case by universal convention — `ANTHROPIC_API_KEY` is
    17, `AWS_SECRET_ACCESS_KEY` is 21 and upper-case, `HUGGING_FACE_HUB_TOKEN`
    is 22 and upper-case — while a 20-character credential is upper-case with
    probability near zero. Mechanically decidable, which is what invariant 4
    asks of a rule that refuses input.

    *A variable that is currently set is always accepted as a name*, before
    either of those. That is both the intended usage and the documented way past
    the other two: a legitimate long lower-case name only has to exist in the
    environment first, and every refusal below says so. The residual — a machine
    where the secret is itself the name of a variable, which `docker run -e
    $TOKEN` produces — is accepted rather than closed, because closing it would
    hard-block a legitimate name with no escape at all.

    *The last rule compares against what is actually exported*, which is what
    catches an upper-case or short token that the length rule lets by. It names
    the matched variable by name only; a name is not a secret.
    """
    if value is None or value == "":
        return ""      # the shipped default for a local runtime: no key needed
    if not isinstance(value, str):
        raise ConfigError(_ENV_SHAPE_ADVICE.format(path=path))
    if not _ENV_NAME_RE.fullmatch(value):
        raise ConfigError(_ENV_SHAPE_ADVICE.format(path=path))
    if value in os.environ:
        return value
    if len(value) >= _ENV_LONG and any(c.islower() for c in value):
        raise ConfigError(_ENV_LOOKS_LIKE_KEY_ADVICE.format(path=path))
    if len(value) >= _ENV_CONTENT_FLOOR:
        for name, held in os.environ.items():
            if held and value in (held, held.strip()):
                raise ConfigError(
                    f"{path} was given the *content* of {name}, not a name — the value "
                    f"is not repeated here. Write the name instead:\n"
                    f"  lx config set {path} {name}")
    return value


def _field_headers(cfg, path, value):
    """Refused: a header value goes onto the wire verbatim.

    `providers.*.headers` is the field a gateway wants `Authorization: Bearer …`
    in, and `lx.config.json` is a file this project's own scaffolder puts into a
    repository — so a command that wrote it would be invariant 6 with one extra
    step. The non-secret uses (an `HTTP-Referer`, an API version) stay reachable
    by hand-editing the file, which is exactly where they are today; what this
    refuses is a *command* that puts a credential there, and a fortiori the HTTP
    endpoint that would one day call the same function.
    """
    # The provider, not the key that was typed: this rule owns the whole block, so
    # `path` may be `providers.local.headers.Authorization` and the advice has to
    # name `providers.local.api_key_env` either way.
    provider = ".".join(path.split(".")[:2])
    raise ConfigError(
        f"{path} is not writable from the command line. A header value is sent to the "
        f"backend verbatim, so this is where a key ends up inside a file meant to be "
        f"committed — keys belong in the environment, named by {provider}.api_key_env. "
        f"Edit lx.config.json by hand for a header that is not one.")


def _field_route(cfg, path, value):
    """A routing value: `local`, `local:qwen2.5:14b-instruct`, or the object form.

    One parser for `lx routing set` and for `lx config set routing.<stage>`,
    because `provider:model` has to mean the same thing in both. It splits on
    the **first** colon only: a model id routinely carries one of its own, and
    the shipped default is `qwen2.5:14b-instruct`.
    """
    stage = path.split(".", 1)[1] if "." in path else ""
    if stage not in ROUTING_STAGES:
        raise ConfigError(
            f"{stage!r} is not a pipeline stage. Known stages: "
            f"{', '.join(ROUTING_STAGES)}, and an entry is written whole — "
            f"`lx routing set draft <provider>[:<model>]`.")
    if isinstance(value, str) and value.strip().startswith("{"):
        value = _as_block(path, value.strip())
    if isinstance(value, dict):
        provider, model = value.get("provider"), value.get("model") or ""
    else:
        provider, _, model = _as_text(
            path, value, "a provider name, optionally `provider:model`").partition(":")
    if not isinstance(provider, str) or not provider.strip():
        raise ConfigError(
            f"{path} names no provider. Write `<provider>` or `<provider>:<model>`.")
    if not isinstance(model, str):
        raise ConfigError(f"{path}: a model id is text.")
    provider, model = provider.strip(), model.strip()
    specs = cfg.get("providers") or {}
    if provider not in specs:
        # The whole value of checking here: today a typo surfaces as a run-time
        # failure, after the extract and however long the person waited.
        raise ConfigError(
            f"unknown provider {provider!r}. Configured: "
            f"{', '.join(sorted(specs)) or 'none'} — `lx providers` lists them with "
            f"their models and their key status.")
    # The bare string is kept when there is no model to add. Every configuration
    # in existence is written that way and `DEFAULT_CONFIG` still ships it, so a
    # writer that upgraded every entry to the object form would quietly make one
    # shape unreachable and turn a compatibility promise into a migration.
    return {"provider": provider, "model": model} if model else provider


#: A field this command decides for itself rather than inferring from what is
#: already there. A pattern is a dotted key with `*` standing for exactly one
#: segment; each rule takes the value as typed *or* as decoded out of a JSON
#: block, and returns what will be written.
_CONFIG_FIELDS = {
    "providers.*.kind": _field_kind,
    "providers.*.base_url": _field_base_url,
    "providers.*.api_key_env": _field_api_key_env,
    "providers.*.headers": _field_headers,
    "providers.*.model": lambda cfg, path, v: _as_text(path, v, "a model id"),
    "providers.*.timeout":
        lambda cfg, path, v: _as_number(path, v, "a number of seconds", low=1),
    "providers.*.temperature":
        lambda cfg, path, v: _as_number(path, v, "a sampling temperature", low=0, high=2),
    "providers.*.max_tokens": lambda cfg, path, v: _as_count(path, v, "a token budget"),
    "providers.*.retries": lambda cfg, path, v: _as_count(path, v, "a retry count", low=0),
    "batch.size": lambda cfg, path, v: _as_count(path, v, "segments per request"),
    "batch.concurrency": lambda cfg, path, v: _as_count(path, v, "parallel requests"),
    "batch.max_repair_rounds":
        lambda cfg, path, v: _as_count(path, v, "a number of repair rounds", low=0),
    "batch.context":
        lambda cfg, path, v: _as_count(path, v, "a number of neighbour segments", low=0),
    "routing.*": _field_route,
}

#: The one pattern whose rule answers for everything below the key as well,
#: because that key may legitimately hold a block. Its rule refuses the whole of
#: it, so `providers.x.headers.Authorization` is refused too. Every *other*
#: pattern decides a single value, and a path reaching inside one is refused by
#: `_addressable` before any rule is asked.
_WHOLE_BLOCK = frozenset(["providers.*.headers"])


def _pattern_matches(pattern, parts):
    names = pattern.split(".")
    return len(names) == len(parts) and all(
        name in ("*", part) for name, part in zip(names, parts))


def _exact_rule(parts):
    """`(pattern, rule)` for this key itself, or `(None, None)`."""
    for pattern, rule in _CONFIG_FIELDS.items():
        if _pattern_matches(pattern, parts):
            return pattern, rule
    return None, None


def _field_rule(parts):
    """The rule that decides this key, or `None`."""
    pattern, rule = _exact_rule(parts)
    if rule:
        return rule
    for length in range(len(parts) - 1, 0, -1):
        pattern, rule = _exact_rule(parts[:length])
        if rule and pattern in _WHOLE_BLOCK:
            return rule
    return None


def _addressable(cfg, parts):
    """Refuse a key that reaches inside something which cannot hold a block.

    Two ways to know, and both are needed — this is one rule with two sources,
    not two rules.

    **The merged configuration types most keys.** `batch.size` is a number,
    `targets` is a list, `length_ratio.zh-TW` is a pair. `set_in` cannot see any
    of that, because it edits the *raw* file and the raw file usually does not
    hold the key at all: on a fresh project `lx config set batch.size.x 1`
    exited 0, wrote `{"batch": {"size": {"x": 1}}}`, and the next `lx translate`
    died inside `_chunks` with a `TypeError`.

    **The field table types the rest**, for a key the merged configuration has
    never seen. Every rule in it decides one value, so a path reaching inside a
    rule's key is a path no rule fires on — which is how
    `lx config set providers.newbackend.api_key_env.x sk_live_…` wrote a raw
    credential into the file with `_field_api_key_env` never consulted. The four
    providers `lx init` scaffolds were incidentally safe, because their
    `api_key_env` is already a string in the raw file and `set_in` refuses to
    descend into one; a backend somebody adds is not, and adding one is the
    normal reason to run this command.

    The same check is what makes `lx config set routing.draft.model` say the true
    thing — `routing.draft` holds one value — rather than the misleading
    `'draft.model' is not a pipeline stage`.
    """
    for length in range(1, len(parts)):
        head = parts[:length]
        prefix = ".".join(head)
        pattern, rule = _exact_rule(head)
        if rule and pattern not in _WHOLE_BLOCK:
            fix = (f"`lx routing set {head[-1]} <provider>[:<model>]`"
                   if pattern == "routing.*" else f"`lx config set {prefix} <value>`")
            raise ConfigError(
                f"{prefix} holds one value, so {'.'.join(parts)} addresses nothing "
                f"inside it. Write {prefix} itself — {fix}.")
        try:
            above = get_in(cfg, head)
        except KeyError:
            continue
        if not isinstance(above, dict):
            raise ConfigError(
                f"{prefix} holds a value, not a block, so {'.'.join(parts)} addresses "
                f"nothing inside it. Write {prefix} itself — "
                f"`lx config set {prefix} <value>`.")


def _decode(cfg, parts, raw):
    """One command-line word as the type its key already holds.

    Type-directed rather than JSON-first, because `lx config set
    providers.openai.model 4` has to write the string `"4"` — a model id is text
    whatever it looks like. JSON is tried only where the merged configuration
    has nothing to take a type from, and a plain string is what is left when it
    does not parse.
    """
    key = ".".join(parts)
    try:
        current = get_in(cfg, parts)
    except KeyError:
        current = MISSING
    if isinstance(current, bool):
        word = raw.strip().lower()
        if word in ("true", "yes", "on", "1"):
            return True
        if word in ("false", "no", "off", "0"):
            return False
        raise ConfigError(f"{key} is true or false — got {raw!r}.")
    if isinstance(current, (int, float)):
        return _as_number(key, raw, "a number")
    if isinstance(current, str):
        return raw
    if isinstance(current, list):
        return _as_list(key, raw)
    if isinstance(current, dict):
        return _as_block(key, raw)
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _validated(cfg, parts, value):
    """`value` with every field rule inside it applied, wherever the field sits.

    A rule keyed on a dotted path has to fire whether the path was typed or
    arrived inside a block: `lx config set providers.openai '{"api_key_env": …}'`
    writes the same leaf as `lx config set providers.openai.api_key_env …`, and a
    check that looked only at the key somebody typed would be decoration. Guarded
    by where a field *lands*, not by how it was addressed — which is the rule
    `web/server.py` already follows for `src` and `lang`.
    """
    rule = _field_rule(parts)
    if rule:
        return rule(cfg, ".".join(parts), value)
    if isinstance(value, dict):
        return {name: _validated(cfg, parts + [name], below)
                for name, below in value.items()}
    return value


def config_value(cfg, key, raw):
    """What `lx config set key raw` will write: `(parts, value)`, decoded then checked."""
    parts = split_key(key)
    _addressable(cfg, parts)
    rule = _field_rule(parts)
    if rule:
        return parts, rule(cfg, key, raw)
    return parts, _validated(cfg, parts, _decode(cfg, parts, raw))


# -- what an untrusted caller may write ------------------------------------

#: Every configuration key writable by something that is **not** a person typing
#: at a terminal — today that is `POST /api/config` and nothing else. Invariant
#: 11's named exception is a command somebody typed, and `do_config_set` inherits
#: it; this list is what does *not* inherit it. It sits beside the field table
#: rather than in `web/server.py` for the reason `confined_path` and
#: `language_tag` do: a rule that exists for the HTTP surface still belongs to
#: the CLI (invariant 8), and a second copy in the server is how two surfaces
#: come to disagree about what is writable.
#:
#: **A literal, not `set(_CONFIG_FIELDS) - _WHOLE_BLOCK`** — which is what it
#: happens to equal today, and a test asserts the subset so it can never exceed
#: it. The two spellings differ in which way they fail. Derived, the day somebody
#: adds a field to `_CONFIG_FIELDS` for an unrelated reason — a `cert_path`, say
#: — that field is writable over HTTP the same afternoon, with nobody deciding
#: it. That is exactly the failure `config.PATH_VALUED_KEYS`' own comment
#: predicts as "a fifth path key added anywhere else". A literal fails the other
#: way: the new field is writable from the command line, and reaching this
#: surface takes an edit here.
#:
#: **What the absences do.** `config.PATH_VALUED_KEYS` — `glossary`, `dnt`,
#: `style`, `output_pattern` — is refused by not appearing, and so is `sources`,
#: which feeds a glob directly and is the fifth path key. `output_pattern` is the
#: one with teeth: `POST /api/render` given no `out` writes to a path formatted
#: from it with confinement deliberately skipped, so a writable `output_pattern`
#: turns a cross-site-reachable endpoint into a file write outside the project.
#: `providers.*.headers` is absent because a header value reaches the backend
#: verbatim. And a long tail with no rule at all — `targets`, `tone`,
#: `source_lang`, `formats.map`, `lexicon_extra`, `checks_disabled`, the `roots`
#: key `docs/decisions.md` reserves — is absent because of the property below.
#:
#: **Every entry has its own single-value rule in `_CONFIG_FIELDS`, and that is
#: load-bearing rather than a coincidence.** `config_value` short-circuits on a
#: rule hit, so for an admitted key `_decode`'s type guessing and `_validated`'s
#: descent into a block are both unreachable — which means the value cannot land
#: anywhere except the key that was addressed. The two bypasses measured on
#: 2026-08-12, `providers.new.api_key_env.x` and `batch.size.x`, are refused here
#: by arithmetic instead: `_pattern_matches` compares segment counts, and neither
#: has a pattern of its own length. So on this surface the where-it-lands class
#: is *unreachable*, not guarded — and `_addressable` and `_validated` stay
#: behind it as the authority on the CLI's own block writes rather than as this
#: surface's only defence. Admitting a key with no rule would end that; the
#: subset test is what makes ending it deliberate.
HTTP_WRITABLE_KEYS = (
    "providers.*.kind",
    "providers.*.base_url",
    "providers.*.api_key_env",
    "providers.*.model",
    "providers.*.timeout",
    "providers.*.temperature",
    "providers.*.max_tokens",
    "providers.*.retries",
    "batch.size",
    "batch.concurrency",
    "batch.max_repair_rounds",
    "batch.context",
    "routing.*",
)

#: The one admitted key a caller must acknowledge by name before the write lands.
#:
#: `base_url` decides where the document under translation is sent — and with it
#: the `Authorization` header built from `api_key_env`, which is read from the
#: environment and handed to whatever this names. Changing it silently is
#: therefore a credential redirect, not a preference, and a confirmation that
#: lived only in a settings screen would be no control at all: the frontend is
#: scheduled to be rebuilt, and this contract anticipates clients this repository
#: did not write.
#:
#: **Removal is a change too**, and that half was measured rather than assumed.
#: `do_config_unset` consults no rule whatsoever — `split_key`, `unset_in`, write
#: — so the gate is the only thing in front of it. Unsetting a key that shadowed
#: a shipped provider's `base_url` restores the factory URL; unsetting a
#: *user-created* provider's leaves the spec without the key at all, and
#: `providers/openai_compat.py` then falls back to a hardcoded
#: `http://localhost:11434/v1`, so the header goes to whatever is listening on
#: that port. Both are "the document now goes somewhere else", which is what the
#: acknowledgement is for, so it is keyed on where the write **lands** rather
#: than on the verb.
#:
#: `api_key_env` deliberately does not need one: removing it stops a credential
#: being sent at all, which is the convergent direction.
_HTTP_CONFIRM_KEY = "providers.*.base_url"


def writable_key(key, confirm_base_url=False):
    """`key` as its segments, or refuse it on behalf of a caller we do not trust.

    Called from the surface that receives the request and never from the CLI —
    the shape `confined_path` and `language_tag` already have, and for the same
    reason: `lx config set output_pattern …` is a person typing a command and
    must go on working.

    The two refusals are deliberately **different exception classes, because the
    status codes have to differ.** The contract says a client distinguishes
    causes by status and by the endpoint it called, never by reading the
    sentence — so if both answered `403`, a settings screen could not tell "this
    key is never writable, give up" from "ask the person and send it again".
    Membership is the first: `UnwritableKey`, which the server answers `403` with
    as a control refusing. The acknowledgement is the second: a `ConfigError`,
    which is a `400` and says what to add to the payload, exactly like every
    other malformed request on that surface.

    No refusal here repeats the value. The gate never looks at one — membership
    is decided from the key alone — and the acknowledgement names only the field
    to send. This matters because the caller may have pasted a credential into
    the wrong box, and a refusal that quotes it has published it to whatever
    renders the sentence.
    """
    parts = split_key(key)
    if not any(_pattern_matches(pattern, parts) for pattern in HTTP_WRITABLE_KEYS):
        raise UnwritableKey(
            f"{'.'.join(parts)} is not writable over HTTP. This surface writes "
            f"{', '.join(HTTP_WRITABLE_KEYS)} and nothing else — a path-valued key, a "
            f"provider's headers, and any key written as a whole block are refused here "
            f"whatever their value. Use `lx config set` in a terminal for the rest.")
    if _pattern_matches(_HTTP_CONFIRM_KEY, parts) and confirm_base_url is not True:
        raise ConfigError(
            f"{'.'.join(parts)} is where the document under translation is sent, and the "
            f"key named by api_key_env goes with it — so this one is not changed on the "
            f"strength of a request alone. Ask first, then send confirm_base_url: true. "
            f"It is the JSON boolean; the string \"true\" is refused.")
    return parts


# -- what may be printed ---------------------------------------------------

def _is_env_name(value):
    return not value or (isinstance(value, str) and bool(_ENV_NAME_RE.fullmatch(value)))


def _printable_spec(spec):
    if not isinstance(spec, dict):
        return spec
    shown = dict(spec)
    if not _is_env_name(shown.get("api_key_env")):
        shown["api_key_env"] = _NOT_A_NAME
    if isinstance(shown.get("headers"), dict):
        shown["headers"] = {name: _HIDDEN_HEADER for name in shown["headers"]}
    if isinstance(shown.get("base_url"), str):
        shown["base_url"] = printable_url(shown["base_url"])
    return shown


def _printable(parts, value):
    """`value` as it may be printed, decided by where it sits rather than by who asked.

    `lx config get providers`, `lx config get providers.openai` and the old → new
    line of `lx config set` all reach this, so they cannot disagree about what is
    printable. A hand-edited file is the case it exists for: `lx config set` will
    not write a key into `api_key_env` or a header at all, and this project has
    no say over what somebody typed into the file directly.
    """
    if not parts or parts[0] != "providers":
        return value
    if len(parts) == 1:
        return ({name: _printable_spec(spec) for name, spec in value.items()}
                if isinstance(value, dict) else value)
    if len(parts) == 2:
        return _printable_spec(value)
    field = parts[2]
    if field == "headers":
        if len(parts) > 3:
            return _HIDDEN_HEADER
        return ({name: _HIDDEN_HEADER for name in value}
                if isinstance(value, dict) else value)
    if field == "api_key_env":
        return value if _is_env_name(value) else _NOT_A_NAME
    if field == "base_url" and isinstance(value, str):
        return printable_url(value)
    return value


def _rendered(parts, value):
    """One printed value: a string bare, anything else as JSON."""
    shown = _printable(parts, value)
    return shown if isinstance(shown, str) else json.dumps(shown, ensure_ascii=False)


# -- the commands ----------------------------------------------------------

def do_config_get(cfg, key=None):
    """The effective value at `key`, as the text `lx config get` prints.

    An `api_key_env` gets the variable's name and whether it is set — never what
    it holds. Reading the value out is the leak invariant 6 exists to prevent,
    and whether a key is present is the whole of what anybody needs here.
    """
    if not key:
        whole = {name: _printable([name], block) for name, block in cfg.items()}
        return json.dumps(whole, ensure_ascii=False, indent=2)
    parts = split_key(key)
    try:
        value = get_in(cfg, parts)
    except KeyError as e:
        raise ConfigError(
            f"no such key: {e.args[0]}. `lx config get` with no key prints the whole "
            f"merged configuration.") from None
    if parts[0] == "providers" and len(parts) == 3 and parts[2] == "api_key_env":
        if not value:
            return "(no key needed for this backend)"
        if not _is_env_name(value):
            return _NOT_A_NAME
        state = "set" if os.environ.get(value) else "not set"
        return f"{value} ({state} in this environment)"
    return _rendered(parts, value)


def do_config_value(cfg, key):
    """The effective value at `key`, keeping its type, as it may be read.

    `do_config_get`'s sibling rather than its replacement, and the difference is
    the audience. That one renders for a terminal — a string bare, everything
    else as JSON text — which is right there and wrong on a wire, where a client
    handed `"12"` for a number has to parse the value back out of a string it was
    given as one.

    Both project through `_printable`, which decides by where a value *sits*
    rather than by who is asking, so a `base_url` that a hand-edited file carries
    a `?key=` in is masked here exactly as `lx config get` masks it. `None` means
    the key has no value at all — which after a removal is the honest answer for
    a key this build ships no default for.
    """
    parts = split_key(key)
    try:
        return _printable(parts, get_in(cfg, parts))
    except KeyError:
        return None


def _write_config(path, data):
    try:
        dump_json(path, data, create_mode=0o600)
    except ConfigError:
        raise                       # the symlink refusal already says what to do
    except OSError as e:
        raise ConfigError(
            f"could not write {path} ({e.strerror or e}) — it is unchanged. Check that "
            f"it is not read-only and not open in another program.") from None
    except ValueError as e:
        # A file this build can read and cannot write back. `json.load` accepts a
        # lone surrogate escape — `"\\ud800"` — and the write then dies at the
        # encode. One sentence and exit 2, like every other refusal here, rather
        # than the traceback and exit 1 that reached the user before.
        raise ConfigError(
            f"{path} holds something that cannot be written back as UTF-8 ({e}) — it "
            f"is unchanged. Open it and remove the offending escape.") from None


def do_config_set(cfg, key, raw, path="lx.config.json"):
    """Write one dotted key into `path`; returns `(old, new)`, `old` is `MISSING` if absent.

    `cfg` is the merged configuration and is what the value is checked against;
    `path` is re-read *raw* and written back, so a key this build does not know —
    one a newer version wrote — survives the round trip untouched, and the file
    goes on holding only what somebody chose rather than a materialized copy of
    every default.

    **This is a terminal-trust writer, and that is the whole of its licence.**
    Invariant 11's named exception is a person typing a command, which is what
    this is; `config.PATH_VALUED_KEYS` lists the four keys that inherit the
    exception through it. It is exactly one import away from being an HTTP
    writer, and on that day either every one of those keys is confined at *use*
    time — `output_pattern` on the result of formatting it, never on the pattern
    — or none of them is writable over HTTP. `append_glossary_rows` below
    carries the other half of the same rule.
    """
    parts, value = config_value(cfg, key, raw)
    data = load_json(path, {})
    old = set_in(data, parts, value)
    _write_config(path, data)
    return old, value


def do_config_unset(key, path="lx.config.json"):
    """Remove a dotted key from `path`; returns the old value, or `MISSING` if it had none."""
    parts = split_key(key)
    data = load_json(path, {})
    old = unset_in(data, parts)
    if old is not MISSING:
        _write_config(path, data)
    return old


def do_routing_set(cfg, stage, target, path="lx.config.json"):
    """Point one stage at a provider, optionally naming a model.

    The stage is checked here as well as inside `_field_route`, because this
    function can be called with a stage that is not one segment — and a
    `routing.a.b` reaching the generic writer would put a key in the file that
    nothing reads.
    """
    if stage not in ROUTING_STAGES:
        raise ConfigError(
            f"{stage!r} is not a pipeline stage. Known stages: "
            f"{', '.join(ROUTING_STAGES)}.")
    return do_config_set(cfg, f"routing.{stage}", target, path)


def _escapes_project(value):
    return isinstance(value, str) and (
        os.path.isabs(value) or ".." in value.replace("\\", "/").split("/"))


def cmd_config_get(args, cfg):
    _out(do_config_get(cfg, args.key))


def _landed_at(pattern, parts, value):
    """Every ``(dotted key, value)`` this write put at a position matching `pattern`.

    **Because a rule is about where a field lands, not where it was addressed**,
    and a *note* about a field is worth no less than a rule about it. The
    validators already reach inside a JSON block — `lx config set providers.x
    '{"base_url": …}'` is checked by `_field_base_url` — while the advisory lines
    below were keyed on `parts[-1]` and so fired for the leaf spelling and stayed
    silent for the block. Measured 2026-08-20: `lx config set providers.x
    '{"base_url": …}'` was validated and then printed nothing at all.
    """
    found, want = [], pattern.split(".")

    def walk(path, node):
        if _pattern_matches(pattern, path):
            found.append((list(path), node))
            return
        if isinstance(node, dict) and len(path) < len(want):
            for name, below in node.items():
                walk(path + [name], below)

    walk(list(parts), value)
    return found


def cmd_config_set(args, cfg):
    old, new = do_config_set(cfg, args.key, args.value, args.config)
    parts = split_key(args.key)
    was = "unset" if old is MISSING else _rendered(parts, old)
    _out(f"{args.key}: {was} → {_rendered(parts, new)}")
    for path, url in _landed_at("providers.*.base_url", parts, new):
        # `path[1]`, never `key.split(".")[1]`. A provider may be named with a
        # dot in it — `lx config set providers '{"a.b": {…}}'` writes one — and
        # splitting the joined key back apart pointed `--provider` at "a".
        _out(f"{'.'.join(path)} is where the document under translation is sent — "
             "check it before the next run")
        # Said, never enforced, and the condition is "no version segment" rather
        # than "does not end in /v1". Both halves were decided on 2026-08-20
        # against a survey of what thirty real clients do, and both have a named
        # failure behind them. *Not enforced*, because a bare
        # `http://127.0.0.1:8088` is a working llama.cpp chat endpoint — measured
        # — and every tool that repaired or refused this has a bug list to show
        # for it. *Not `/v1`*, because Continue.dev told a `/v2` user they had
        # forgotten `/v1` (#7682), and `/api/v1` and `/v1beta` are ordinary.
        #
        # It names the key and not the value: the line above already printed the
        # value, and a second display surface for a `base_url` is a thing to add
        # deliberately or not at all — invariant 6, and the two surfaces that
        # were found missing from that list on 2026-08-13.
        if not has_version_segment(url):
            _out(f"  note: its path carries no API version segment, where a hosted "
                 f"endpoint usually has one. Not an error — a local runtime or a "
                 f"proxy behind a prefix may be exactly this. "
                 f"`lx models --provider {path[1]}` asks it directly.")
    if parts[-1] == "api_key_env" and new and not os.environ.get(new):
        _out(f"{new} is not set in this environment yet; export it before running")
    if args.key in PATH_VALUED_KEYS and _escapes_project(new):
        _out("that path leaves the project directory. `lx` opens it as typed, which is "
             "what a path typed at a terminal gets (invariant 11) — the workbench does "
             "not get the same licence")


def cmd_config_unset(args, cfg):
    old = do_config_unset(args.key, args.config)
    if old is MISSING:
        _out(f"{args.key} was not set in {args.config}; the default already applies")
        return
    try:
        now = f"the default ({do_config_get(load_config(args.config), args.key)})"
    except ConfigError:
        # A key this build has no default for — one a newer version wrote, or a
        # note somebody kept in the file. Reporting that is better than failing
        # here, because by this line the removal has already happened.
        now = "nothing; this build has no default for it"
    _out(f"{args.key}: {_rendered(split_key(args.key), old)} → {now}")


def _route_line(cfg, stage):
    """One line of `lx routing show`: what the entry says, then what it resolves to."""
    try:
        provider, entry_model = route_entry(cfg, stage)
        _, model = resolve_route(cfg, stage)
    except ConfigError as e:
        return f"{stage} → {e}"
    if entry_model:
        # The model the *entry* names is the override, and it is what this
        # command exists to make visible.
        line = f"{stage} → {provider} ({entry_model})"
    elif model:
        line = f"{stage} → {provider} ({model}, from the provider)"
    else:
        line = f"{stage} → {provider}"
    if provider not in (cfg.get("providers") or {}):
        line += "  ← not configured; `lx providers` lists what is"
    return line


def cmd_routing_show(args, cfg):
    for stage in ROUTING_STAGES:
        _out(_route_line(cfg, stage))


def cmd_routing_set(args, cfg):
    old, new = do_routing_set(cfg, args.stage, args.target, args.config)
    was = "unset" if old is MISSING else json.dumps(old, ensure_ascii=False)
    _out(f"routing.{args.stage}: {was} → {json.dumps(new, ensure_ascii=False)}")
    _out(_route_line(load_config(args.config), args.stage))


# ── translate / repair / run ───────────────────────────────────────────────

def cmd_providers(args, cfg):
    from .providers import available
    for p in available(cfg):
        key = "no key needed" if not p["needs_key"] else (
            f"{p['key_env']} set" if p["key_present"] else f"{p['key_env']} MISSING")
        _out(f"{p['name']:12} {p['kind']:10} {p['model']:28} {p['base_url']:34} {key}")
        if p.get("error"):
            # A row whose fields are blank because the block could not be read.
            # Without this the command answers a hand-edited mistake with a line
            # of padding and a plausible "no key needed", which is the silence
            # the projection was changed to stop.
            _out(f"  ← {p['error']}")
    _out("\nrouting: " + "  ".join(_route_word(cfg, s) for s in ROUTING_STAGES))


def do_models(cfg, provider=None):
    """What a backend says it serves: ``(name, configured_model, rows)``.

    `rows` is a list of ``{"id", "status"}`` from `Provider.list_models`, sorted
    by id. `configured_model` is what this project would send today, resolved
    through `config.resolve_route` exactly as a run would resolve it — so the
    two answers can be compared, which is the only reason to print them
    together.

    **The stage is `draft`.** Not a parameter, because a listing is a property of
    a *backend* and every stage that reaches one reaches the same endpoint;
    `--provider` is how you ask a different backend, and that is the axis that
    exists. A `--stage` flag would let two spellings of "ask llamacpp what it
    has" disagree.

    **Advisory, and it does not gate anything.** Nothing checks a configured
    `model` against this list. A backend may serve a model it does not
    enumerate — a single-model `llama-server` ignores the field entirely and
    still answers — and a list that became a gate would refuse a working
    configuration on the strength of an endpoint the OpenAI-compatible world
    treats as optional.
    """
    from .providers import build
    name, configured = resolve_route(cfg, "draft", provider)
    return name, configured, build(name, cfg, configured).list_models()


def cmd_models(args, cfg):
    from .providers import available
    name, configured, rows = do_models(cfg, args.provider)
    if args.json:
        _out(json.dumps({"provider": name, "configured": configured, "models": rows},
                        ensure_ascii=False, indent=2))
        return
    # `printable_url` rather than the raw `base_url`, for the reason invariant 6
    # gives: this is a display surface, and the enumerated list of them is a
    # symptom of the rule rather than its definition. Taken from `available`,
    # which already masks, so there is no second answer about what is printable.
    spec = next((p for p in available(cfg) if p["name"] == name), {})
    _out(f"{name} ({spec.get('kind', '')} @ {spec.get('base_url', '')}) · "
         f"{len(rows)} model(s)")
    if not rows:
        _out("  the backend answered an empty list")
    # Capped, because the column is padded to the widest row and the rows came
    # from the backend. `Provider._sane` already refuses a field longer than
    # `_MAX_FIELD`, so this is the second of two — but the first is in a class a
    # future backend might not use, and a padding width is the kind of arithmetic
    # that turns an untrusted number into 400 MB of stdout.
    width = min(max((len(m["status"]) for m in rows), default=0), 12)
    for m in rows:
        # The marker is on the *configured* model, not on a loaded one. Which
        # model is resident is the backend's business and changes by itself;
        # which one this project would send is the answer the reader came for.
        mark = "  ← configured" if m["id"] == configured else ""
        _out(f"  {m['status']:{width}}  {m['id']}{mark}" if width else
             f"  {m['id']}{mark}")
    # **Name the key the value actually came from.** `configured` is resolved
    # most-specific-first, so a `routing.draft` of `{"provider": …, "model": …}`
    # supplies it and `providers.<name>.model` does not — and both lines below
    # used to say `providers.<name>.model` regardless. The result was a note
    # quoting a value that `lx config get providers.<name>.model` did not return,
    # and a remedy that changed nothing when followed. Found by the adversarial
    # pass, 2026-08-20.
    _, from_entry = route_entry(cfg, "draft")
    if from_entry and not args.provider:
        holder, remedy = "routing.draft", f"lx routing set draft {name}:<id>"
    else:
        holder, remedy = f"providers.{name}.model", f"lx config set providers.{name}.model <id>"
    if configured and not any(m["id"] == configured for m in rows):
        # **Say when the list was cut**, or this note accuses a backend of not
        # serving a model it may well serve: `Provider._listing` caps the rows,
        # so on a backend publishing more than the cap "did not list" means "is
        # not in the part we kept". Nothing else here can tell the two apart.
        from .providers.base import _MAX_ROWS
        cut = (f" The listing was cut at the first {_MAX_ROWS} ids, so it may "
               f"serve this one anyway." if len(rows) >= _MAX_ROWS else "")
        _out(f"\nnote: {holder} is {configured!r}, which this backend did not list.{cut} "
             f"A single-model server ignores the field and will still answer; a "
             f"router will refuse with 400.")
    _out(f"\nto use one: {remedy}")


def _route_word(cfg, stage):
    """`stage=provider[:model]` — the spelling `lx routing set` takes back."""
    try:
        provider, model = route_entry(cfg, stage)
    except ConfigError:
        return f"{stage}=(malformed; `lx routing show` says how)"
    return f"{stage}={provider}" + (f":{model}" if model else "")


def _model_writable(segments, over_human):
    """Drop what an ``llm:*`` write would be refused at the door.

    **Selection has to know the rule the write enforces.** Origin precedence
    lives in `store`, at the write, which is what makes it hold for every writer
    — but a queue that cannot see it hands the model work it will pay for and
    then throw away. Measured by an adversarial pass on 2026-08-16: `lx repair`
    selected a human-written failing segment, was refused, exited **0** with the
    error count unmoved, and did the same again on the next invocation; and
    `lx translate --mode polish` on a 2000-paragraph reviewed novel selected all
    two thousand and applied none of them. Neither is visible on a four-segment
    document, which is the size everything here was verified at.

    This is the argument the hold exclusion already makes — a predicate that
    selects work must not select work no run may do — applied to the other new
    rule, which has the identical property. ``over_human`` turns it off here
    exactly as it turns the write's own guard off, so the two can never disagree
    about what a run will touch.

    Not applied to an explicitly named ``ids``, for the reason the hold is not:
    naming an id is a person pointing at one segment. What they get there is a
    refusal at the write, with a sentence naming ``--overwrite-human``.
    """
    if over_human:
        return segments
    return [s for s in segments if s.get("origin") != HUMAN]


def do_select(doc, cfg, mode, ids=None, include_all=False, limit=0, over_human=False):
    """Which segments a run of ``mode`` works on. One answer for every surface.

    The rule used to live in three places and they disagreed. `cmd_translate`
    read `--mode repair` as *pending* segments because it had no repair branch at
    all, while `POST /api/translate` read `mode: "repair"` as the segments a
    fresh check rejects — two surfaces of one product answering the same question
    differently, with the server silently picking one. That is
    `docs/contracts/workbench-http.md` divergence (2), and the settlement is that
    **`repair` means failing segments on both**, decided in one function neither
    surface may branch around. The mirror settlement — making the wire mean
    pending — was refused: it bumps the contract version and turns a Repair
    button into "translate the rest of the book".

    `ids` is tested **first**, which is the wire's order and was not the CLI's:
    `lx translate --mode polish --ids s3` used to ignore `--ids` entirely,
    because the polish branch came before it. An explicit id is a person naming
    the work, so it outranks the mode — and it is deliberately *not* filtered by
    anything else, which is what makes the sentence `do_apply` prints
    (`lx translate --ids <id>`) true whatever state the segment is in.

    `include_all` reaches only the pending branch, as it always has: `--all`
    means "everything, not only the pending ones", which the other three
    branches each answer for themselves.

    **`limit` bounds every branch except a named `ids`, and it did not until
    2026-09-02.** It reached the pending branch alone, so a `--limit 20` was
    silently inert on `polish` and on `repair` — and `_model_writable`'s own
    docstring measures what that costs: `lx translate --mode polish` on a
    2000-paragraph novel selects all two thousand. A bound that binds one of the
    three buttons on a shared toolbar is a control that lies about the other
    two, and the wire could not express one at all. This is not a second
    predicate — the red line's concern — but strictly fewer: one cap, applied
    once, to whatever the branch decided, where the pending branch used to carry
    its own. `docs/decisions.md`, 2026-09-02.

    The cap runs **after** the exclusions rather than before, which is the
    argument `pending_segments` already makes for the hold, applied to the other
    rule that removes work: a run of segments a person wrote used to eat a
    `--limit 20` and hand back four. Measured at `67629fd` —
    `lx translate --all --limit 20` on a document whose first thirty segments
    are `origin: human` selected **nothing**, while the unbounded call selected
    ten.

    **A held segment is excluded from every branch except `ids`.** That exemption
    is the design and not an oversight: holding says "no *queue* may take this",
    and naming an id is a person pointing at one segment. It is also what keeps
    `do_apply`'s own refusal message honest — it tells a reviewer to run
    `lx translate --ids <id>`, and a hold silently swallowing that would make the
    sentence false. The model still cannot overwrite their wording, because
    origin precedence is a separate rule enforced at the write.
    """
    # Checked before `ids` short-circuits, and applied after. A bound this call
    # will not use is still a bound the caller got wrong, and accepting it here
    # means the mistake surfaces on the day they stop sending `ids` — as a run
    # that translates a whole book. Shape and precedence are two questions.
    limit = checked_limit(limit)
    if ids:
        # Naming ids is a person pointing at segments, so the bound is not
        # applied to them: truncating would silently drop work they asked for.
        # `mode` is already outranked here for the same reason.
        wanted = set(ids)
        return [s for s in doc["segments"] if s["id"] in wanted]
    if mode == "repair":
        # Lazily, like every other reach into `translate` from here: importing it
        # pulls in the provider stack, and `do_select` is called on paths that
        # never dispatch to a model.
        from .translate import failing_segments
        picked = failing_segments(doc, cfg)
    elif mode == "polish":
        picked = workable([s for s in doc["segments"]
                           if s.get("target") and s["kind"] in ("para", "quote", "list")])
    else:
        # `--all` is where this reaches the draft queue: a pending segment has no
        # target and therefore no origin, but `include_all` ignores `status`
        # wholly. `limit=0` on purpose — the cap below is the one that applies,
        # and applying it here as well would put it back before the exclusion.
        picked = pending_segments(doc, include_all=include_all)
    return bounded(_model_writable(picked, over_human), limit)


def do_translate(src, lang, cfg, segments, mode, provider=None, model=None,
                 batch=None, concurrency=None, progress=None, on_batch=None,
                 over_human=False, on_usage=None):
    """Run a model over these segments and bank each batch.

    ``(applied, failures, refused)``, where ``refused`` names the segments a
    person had already written and this run therefore left alone — reported
    rather than dropped in silence, because a run that says "translated 40" while
    having skipped four is a report nobody can act on. ``over_human`` is the way
    past the guard, and it is a deliberate act on every surface rather than a
    default.

    Plain parameters rather than an argparse ``Namespace``, because the workbench
    calls this too. It used to be a private `_translate` that read `args.model`
    and friends off a `Namespace`, so `web/server.py` could not call it and had
    assembled its own copy of the same six lines — which is how the two surfaces
    came to disagree about what `repair` selects in the first place.

    ``progress`` is a plain sink taking one line; the lock that makes it safe to
    call from several worker threads is put on here rather than asked of the
    caller. ``on_batch`` is handed ``(written, refused)`` for the batch that just
    landed, so a caller reporting progress while the run is still going — the job
    table does — has both numbers moving rather than one of them appearing at the
    end. ``on_usage`` is handed this run's token totals once, on every path
    including the one that raises; the sentence describing them has already gone
    to ``progress`` by then, so a caller wanting only the printed line passes
    nothing.
    """
    from .translate import Progress, translate_segments
    doc = load_doc(src, lang)
    if not segments:
        return 0, [], []
    origin = f"llm:{mode}"
    applied, refused = 0, []

    def commit(ok):
        """One batch, durable the moment it lands, and counted as it lands.

        The `do_apply` that used to run over every result *again* at the end is
        gone. It wrote nothing `save_targets` had not already written — `accept`
        normalizes, repairs and reseats before either of them sees the text, and
        both set the same `status` and `origin` and clear the same issues — so
        its only effect was to rewrite every segment of the run at the end, over
        whatever a reviewer had edited in the meantime. Deleting it shrinks that
        window from the whole run to one batch.

        Deleted on **both** surfaces in one move, deliberately. The first version
        of that change removed it from the job only, which left `lx translate` —
        the surface invariant 8 calls the product — carrying the wider exposure
        the change claimed to have closed. Adversarial review, 2026-08-14. There
        is one copy of it now, which is what makes that class of divergence
        unreachable rather than repaired.

        No lock: `translate_segments` calls `on_batch` under the same lock that
        guards its own results, so these calls are already serialized.
        """
        nonlocal applied
        written, left_alone = save_targets(src, lang, ok, origin, over_human=over_human)
        applied += written
        refused.extend(left_alone)
        if left_alone and progress:
            progress(f"  left {len(left_alone)} segment(s) alone: a person wrote them "
                     f"({', '.join(sorted(left_alone))})")
        if on_batch:
            on_batch(written, sorted(left_alone))

    _results, failures = translate_segments(
        segments, doc, cfg, provider_name=provider, mode=mode,
        batch_size=batch, concurrency=concurrency,
        progress=Progress(progress) if progress else None,
        on_batch=commit, model=model, on_usage=on_usage)
    return applied, failures, sorted(refused)


def _run_translate(src, lang, cfg, segments, mode, args):
    """`do_translate` with the terminal's own reporting around it.

    ``(applied, failures, usage)``, where ``usage`` is `translate.NO_USAGE`'s
    shape so `cmd_run` can total the several passes it makes, or **`None` when
    no model was called at all** — which is not the same as a run that cost
    nothing, and is also what keeps the two early returns below from importing
    `translate` and with it the whole provider stack. The *sentence* about the
    numbers has already been printed by `translate_segments` through
    `progress`, which is why nothing here formats one. Private and
    `Namespace`-coupled, so widening it costs the five call sites in this file
    and nothing else.
    """
    if not segments:
        _out("nothing to do")
        return 0, [], None
    if args.dry_run:
        chars = sum(len(s["masked"]) for s in segments)
        provider, model = resolve_route(cfg, mode, args.provider, args.model)
        # No token figure here, and that is a decision rather than an omission:
        # counting them before a run needs a tokenizer, which is a compiled
        # dependency under invariant 1. "Source characters" is the honest proxy
        # this command already chose, and a bounded run is the control. See
        # `docs/decisions.md`, 2026-09-02.
        _out(f"dry run: {len(segments)} segment(s), {chars} source characters, "
             f"mode={mode}, provider={provider}, model={model or 'unset'}")
        return 0, [], None
    spent = {}
    applied, failures, refused = do_translate(
        src, lang, cfg, segments, mode, provider=args.provider, model=args.model,
        batch=args.batch, concurrency=args.concurrency, progress=_out,
        over_human=args.overwrite_human, on_usage=spent.update)
    for sid, why in failures:
        _out(f"  unresolved {sid}: {why}")
    if refused:
        _out(f"{len(refused)} segment(s) were left alone because a person wrote them. "
             f"Pass --overwrite-human to replace them anyway.")
    return applied, failures, spent or None


def _report_limit(selected, limit, ids=None):
    """Say that a bound stopped this run, without promising what a next one does.

    **The bound takes the front of the selection; it does not advance.** For the
    draft queue the two look identical, because translating a pending segment
    removes it — so the next run gets the next N and "run again for the rest" is
    true. For `polish` it is false: a polished segment is still translated
    prose, so it is still selected, and a bounded polish asks for the *same*
    head every time. Measured 2026-09-02 against a real backend: three
    consecutive `--mode polish --limit 3` runs asked for `s0001, s0002, s0003`
    on each of them. The first version of this sentence said "run the same
    command again for the rest" for every mode, which is an instruction that
    silently does nothing on two of the four and bills for it.

    So this states the fact and stops. Making a bounded polish *advance* would
    need per-mode progress state — which segment was polished at which version —
    and that is a queue this project does not have.

    Only when the selection came back **exactly** at the cap, because that is the
    one thing decidable without a second pass, and a second pass is not free:
    `mode="repair"` selection runs every validator over every segment, so asking
    twice doubles that on a novel.

    Silent when `ids` named the work: the bound is documented as ignored there,
    so announcing it would describe a rule that did not apply.
    """
    if ids or not limit or len(selected) != limit:
        return
    _out(f"stopped at the --limit of {limit}; anything past it was not sent")


def cmd_translate(args, cfg):
    doc = load_doc(args.src, args.lang)
    ids = args.ids.split(",") if args.ids else None
    segments = do_select(doc, cfg, args.mode, ids=ids,
                         include_all=args.all, limit=args.limit,
                         over_human=args.overwrite_human)
    applied, failures, _usage = _run_translate(
        args.src, args.lang, cfg, segments, args.mode, args)
    _out(f"translated {applied} segment(s)" + (f", {len(failures)} unresolved" if failures else ""))
    _report_limit(segments, args.limit, ids)


def _unrepairable(doc, cfg):
    """Failing segments no run may touch, and which reason. ``(held, by_hand)``.

    `lx check` counts their errors in its exit code — it walks every segment,
    because a structural error is a structural error whoever wrote the sentence —
    and the repair pass can select neither kind. Without this the two commands
    contradict each other in silence: `lx check` exits 1 while `lx repair`
    answers "nothing failing", or worse pays a model for a segment whose write it
    then refuses.
    """
    from .translate import failing_segments
    failing = failing_segments(doc, cfg, include_held=True)
    return ([s["id"] for s in failing if is_held(s)],
            [s["id"] for s in failing if not is_held(s) and s.get("origin") == HUMAN])


def _report_blockers(src, lang, held, by_hand):
    if held:
        _out(f"{len(held)} failing segment(s) are held and no queue will select "
             f"them: {', '.join(held)}. Fix the wording yourself, or return them "
             f"with `lx unhold {src} --lang {lang} --ids {','.join(held)}`.")
    if by_hand:
        _out(f"{len(by_hand)} failing segment(s) were written by a person and a "
             f"model run may not replace them: {', '.join(by_hand)}. Fix the "
             f"wording yourself, or pass --overwrite-human to let the run try.")


def cmd_repair(args, cfg):
    do_check(args.src, args.lang, cfg)
    doc = load_doc(args.src, args.lang)
    # `--limit` reaches here because the wire can bound a repair run and a
    # command that could not would be the CLI-lacks-what-the-wire-has shape
    # invariant 8 exists to stop — divergence (30) is the standing example.
    segments = do_select(doc, cfg, "repair", limit=args.limit,
                         over_human=args.overwrite_human)
    if not segments:
        held, by_hand = _unrepairable(doc, cfg)
        if held or by_hand:
            _report_blockers(args.src, args.lang, held, by_hand)
        else:
            _out("nothing failing")
        return
    _out(f"repairing {len(segments)} failing segment(s)")
    _run_translate(args.src, args.lang, cfg, segments, "repair", args)
    report, _ = do_check(args.src, args.lang, cfg)
    _out(f"after repair: {report['errors']} error(s), {report['warnings']} warning(s)")
    # After the count, not before it: a reader who bounded a repair needs to see
    # that errors remaining is what they asked for rather than a repair that
    # failed. This is the "with no sign of why" the old pending-branch-only rule
    # was defending against, answered by saying why.
    _report_limit(segments, args.limit)


def cmd_run(args, cfg):
    """extract → translate → check → repair* → render, in one command.

    ``--limit N`` bounds **each model pass** at N segments, and the repair
    rounds are narrowed to the segments this command itself sent. That second
    half is not tidiness, it is the whole of the flag: an untranslated segment
    fails `checks.check_segment`'s `missing` rule at *error* severity, so
    `do_select(mode="repair")` returns every segment the bound left alone, and
    without the narrowing repair round 1 would translate the entire remainder —
    spending the same money, stamping it `llm:repair` instead of `llm:draft`,
    and printing "repair round 1/3: 980 failing segment(s)" as though that were
    normal. Verified on this build at `67629fd`. Unbounded, nothing is narrowed
    and every path is what it was: a run that repairs a carryover wording it did
    not itself write is the behaviour that would otherwise be lost.

    Rendering needs **no new rule**, and that is the third option the package
    that scheduled this did not have: the gate already here — do not render
    while errors remain — is exactly right for a bounded run, because the work
    it deliberately left undone *is* an error. A bounded run that happens to
    finish the document has none and renders normally. Only the sentence had to
    change: "inspect with `lx check`" is wrong advice when the reason is that
    somebody asked for fifty of three hundred.
    """
    doc, reused, rejected, notes = do_extract(args.src, args.lang, cfg, args.tone)
    # Through `do_select` rather than inline, so this is not a fourth spelling of
    # the draft queue's predicate. It was one until 2026-08-15.
    pending = do_select(doc, cfg, "draft", limit=args.limit,
                        over_human=args.overwrite_human)
    _out(f"{args.src} [{args.lang}] · {len(doc['segments'])} segments · "
         f"{reused} reused · {len(pending)} to translate"
         + (f" · {rejected} stale proposal(s) refused" if rejected else ""))
    # The same four lines `lx extract` prints. This command takes `--tone` too,
    # and without this a register change emptied a reviewed book here while
    # printing a line indistinguishable from a first run.
    report_extract(args.src, args.lang, notes)

    # The ids this command sent to a model, in the order the passes sent them.
    # It is what the repair rounds are narrowed to under `--limit`, and it has
    # to be what was *selected* rather than what is still pending at the end, or
    # a segment the model was asked for and failed on would be quietly excused
    # from the repair it exists for.
    touched, spent, passes = set(), None, 0

    def pass_over(segments, mode):
        """One model pass, counted into this command's own totals."""
        nonlocal spent, passes
        touched.update(s["id"] for s in segments)
        _applied, _failures, usage = _run_translate(
            args.src, args.lang, cfg, segments, mode, args)
        if not usage or not usage["replies"]:
            return
        # Local, like every other reach into `translate` from this module: a
        # `lx run` over a finished document reaches no model and must not pay
        # for importing the provider stack to say so.
        from .translate import usage_add
        spent = usage if spent is None else usage_add(spent, usage)
        passes += 1

    if pending:
        pass_over(pending, "draft")
    if args.polish:
        prose = do_select(load_doc(args.src, args.lang), cfg, "polish",
                          limit=args.limit, over_human=args.overwrite_human)
        _out(f"polishing {len(prose)} prose segment(s)")
        pass_over(prose, "polish")

    rounds = args.max_rounds if args.max_rounds is not None else cfg.get("batch", {}).get("max_repair_rounds", 3)
    previous = None
    for attempt in range(rounds):
        report, _ = do_check(args.src, args.lang, cfg)
        if not report["errors"]:
            break
        bad = do_select(load_doc(args.src, args.lang), cfg, "repair",
                        limit=args.limit, over_human=args.overwrite_human)
        if args.limit:
            # Narrowing the answer, never asking a different question:
            # `do_select` is still the one place that decides what "failing"
            # means, and this is the command scoping the round to its own work.
            # Only under `--limit`, so an unbounded run keeps repairing a
            # carryover wording it did not write.
            #
            # The bound is passed to `do_select` **as well as** this filter, and
            # both are needed. `touched` can hold up to 2N after a draft pass and
            # a `--polish` pass, so the filter alone would let a repair round
            # send 2N and make "at most N per pass" false — the promise in this
            # flag's own help text. The filter alone is also not enough the other
            # way: without it the round selects every failing segment in the
            # document, which after a bounded draft is the whole untranslated
            # remainder.
            bad = [s for s in bad if s["id"] in touched]
        if not bad:
            break
        signature = {s["id"]: s.get("target") for s in bad}
        if signature == previous:
            _out("repair made no difference last round; stopping so it does not spin")
            break
        previous = signature
        _out(f"repair round {attempt + 1}/{rounds}: {len(bad)} failing segment(s)")
        pass_over(bad, "repair")

    report, _ = do_check(args.src, args.lang, cfg)
    _out(f"check: {report['errors']} error(s), {report['warnings']} warning(s)")
    # Only when more than one pass reached a model. A single pass has already
    # printed this exact sentence through `progress`, and repeating the same
    # numbers under a second label reads as a second charge.
    if passes > 1:
        from .translate import usage_line
        total = usage_line(spent, label="run total")
        if total:
            _out(total)
    if report["errors"] and not args.force:
        # Named before the general advice, because "inspect with `lx check`"
        # sends a reviewer to a command that will show them errors on a segment
        # every repair round silently skipped.
        doc = load_doc(args.src, args.lang)
        held, by_hand = _unrepairable(doc, cfg)
        _report_blockers(args.src, args.lang, held, by_hand)
        # **Asked, not assumed.** The bounded sentence is only true while a
        # further run would still take something, so it is gated on the draft
        # queue rather than on `--limit` being set. The first version of this
        # gated on the flag alone and was measured saying "the rest of the
        # document is still untranslated. Run the same command again to
        # continue" to somebody whose document was **12 of 12 translated** and
        # for whom running again did nothing at all — the errors were on a
        # segment a person had written, which no run may replace. A message
        # that names the wrong cause is worse than the general one it replaced,
        # because it sends the reader to a remedy that cannot work.
        more = do_select(doc, cfg, "draft", over_human=args.overwrite_human)
        if args.limit and more:
            # Sent to `lx check` here a reader would find errors they created on
            # purpose and conclude the run had gone wrong.
            _out(f"not rendering: this run was bounded to {args.limit} segment(s) per pass, "
                 f"and {len(more)} segment(s) are still untranslated. Run the same command "
                 f"again to continue, or pass --force to render what there is.")
        else:
            _out("not rendering while errors remain — inspect with `lx check` or fix in `lx web`, "
                 "or pass --force to render anyway")
        sys.exit(1)
    out = args.out or default_output(args.src, args.lang, cfg)
    text, missing = do_render(args.src, args.lang, cfg, fallback=args.force)
    write_document(out, text)
    # `missing` was bound and thrown away here, where `cmd_render` has always
    # printed it. It costs nothing and it is the whole signal that `--force` on
    # a bounded run wrote a document whose untranslated segments fell back to
    # the *source text* — an English book with twenty Chinese paragraphs in it,
    # which is not something to discover by reading the file.
    _out(f"wrote {out}" + (f" ({missing} untranslated)" if missing else ""))
    _out("review the rendered file, then `lx commit` to bank the wording in the translation memory")


def cmd_web(args, cfg):
    from .web.server import serve
    serve(host=args.host, port=args.port, open_browser=not args.no_browser)


# ── parser ─────────────────────────────────────────────────────────────────

#: One sentence for every `--limit` that bounds a model run, because the flag
#: means the same thing on each of them and two wordings is how they stop doing
#: so. `lx todo --limit` is deliberately not one of these: it truncates a
#: listing and spends nothing.
_LIMIT_HELP = ("most segments this run sends to the model; 0 for all of them. "
               "Taken from the top of the selection, so it bounds spend rather "
               "than walking through the document: the draft queue drains and "
               "the next run takes the next ones, but a bounded --mode polish "
               "asks for the same segments every time. Ignored when --ids "
               "names the work")


def _add_llm_flags(p):
    p.add_argument("--provider", help="provider name from lx.config.json; overrides routing")
    p.add_argument("--model", help="model id for this run; overrides the routing entry's "
                                   "model and the provider's own. A --provider that names "
                                   "a different backend drops the entry's model, since a "
                                   "model id belongs to the backend that serves it")
    p.add_argument("--batch", type=int, help="segments per request")
    p.add_argument("--concurrency", type=int, help="parallel requests")
    p.add_argument("--dry-run", action="store_true", help="report the work without calling a model")
    p.add_argument("--overwrite-human", action="store_true",
                   help="let this run replace segments a person wrote. Off by "
                        "default: an unattended pass runs over whatever the queue "
                        "hands it, and review is the thing it would overwrite")


def build_parser():
    p = argparse.ArgumentParser(prog="lx", description="Scriptorium localization pipeline")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", default="lx.config.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="scaffold config and state").set_defaults(fn=cmd_init)
    sub.add_parser("providers", help="list configured backends").set_defaults(fn=cmd_providers)

    md = sub.add_parser(
        "models", help="ask a backend which models it serves",
        description="Ask a configured backend what it serves, so a model id can be "
                    "copied rather than typed. A llama.cpp server in router mode "
                    "selects on an exact id and answers 400 for anything else, and "
                    "its ids are long; this is how you find them.")
    md.add_argument("--provider", help="provider name from lx.config.json; "
                                       "default is whatever routing.draft names")
    md.add_argument("--json", action="store_true", help="machine-readable output")
    md.set_defaults(fn=cmd_models)

    cf = sub.add_parser("config", help="read and write lx.config.json")
    cf_sub = cf.add_subparsers(dest="action", required=True)
    cf_get = cf_sub.add_parser(
        "get", help="print the effective value; with no key, the whole merged configuration")
    cf_get.add_argument("key", nargs="?", help="dotted, as in providers.local.model")
    cf_get.set_defaults(fn=cmd_config_get)
    cf_set = cf_sub.add_parser(
        "set", help="write one dotted key",
        description="Write one dotted key into lx.config.json. Keys never carry a "
                    "credential: api_key_env takes the NAME of an environment variable, "
                    "and no `lx` command takes key material on a command line — argv is "
                    "visible in a process listing and lands in shell history, which no "
                    "later refusal can undo.")
    cf_set.add_argument("key")
    cf_set.add_argument("value")
    cf_set.set_defaults(fn=cmd_config_set)
    cf_unset = cf_sub.add_parser("unset", help="remove a key so the default applies again")
    cf_unset.add_argument("key")
    cf_unset.set_defaults(fn=cmd_config_unset)

    ro = sub.add_parser("routing", help="which backend serves each pipeline stage")
    ro_sub = ro.add_subparsers(dest="action", required=True)
    ro_sub.add_parser(
        "show", help="stage → provider, with the model when the entry names one"
    ).set_defaults(fn=cmd_routing_show)
    ro_set = ro_sub.add_parser("set", help="point a stage at a provider")
    ro_set.add_argument("stage", choices=list(ROUTING_STAGES))
    ro_set.add_argument("target", metavar="provider[:model]",
                        help="a provider name, or provider:model to run that stage on a "
                             "different model at the same endpoint. The model may contain "
                             "colons of its own — only the first one splits")
    ro_set.set_defaults(fn=cmd_routing_set)

    e = sub.add_parser("extract", help="parse a document into segments")
    e.add_argument("src")
    e.add_argument("--lang", required=True)
    e.add_argument("--tone", help="register for this document: technical (default) or "
                                  "literary. Frozen on the document and kept by later "
                                  "extracts; changing it leaves the existing translations "
                                  "behind, so run `lx commit` first")
    e.add_argument("--reset", action="store_true",
                   help="discard the existing state instead of carrying its translations "
                        "over. It does not read the old state at all, which is why it "
                        "cannot recover the register: pass --tone with it, or the command "
                        "is refused")
    e.set_defaults(fn=cmd_extract)

    t = sub.add_parser("todo", help="emit pending segments as JSON")
    t.add_argument("src")
    t.add_argument("--lang", required=True)
    t.add_argument("--all", action="store_true")
    t.add_argument("--limit", type=int, default=0)
    t.set_defaults(fn=cmd_todo)

    tm = sub.add_parser("terms", help="propose glossary rows from the source text")
    tm.add_argument("src")
    tm.add_argument("--lang", required=True)
    tm.add_argument("--min-count", type=int, default=None,
                    help="occurrences before a candidate is proposed; default from the "
                         "`terms` block of lx.config.json")
    tm.add_argument("--append", action="store_true",
                    help="add candidates the glossary does not have; existing rows are "
                         "never rewritten or reordered")
    tm.add_argument("--json", action="store_true")
    tm.set_defaults(fn=cmd_terms)

    a = sub.add_parser("apply", help="ingest translations")
    a.add_argument("src")
    a.add_argument("--lang", required=True)
    a.add_argument("--file", default="-", help="'-' reads stdin")
    a.add_argument("--origin", default="agent")
    # Here as well as on the three model-calling commands: `--origin` takes free
    # text, so `lx apply --origin llm:draft` reaches the same guard, and until
    # 2026-08-16 that path was refused with no way past it at all.
    a.add_argument("--overwrite-human", action="store_true",
                   help="let this write replace segments a person wrote")
    a.set_defaults(fn=cmd_apply)

    for name, lift, blurb in (
            ("hold", False, "keep segments out of every queue that selects work"),
            ("unhold", True, "return held segments to the queues")):
        h = sub.add_parser(name, help=blurb)
        h.add_argument("src")
        h.add_argument("--lang", required=True)
        h.add_argument("--ids", required=True, help="comma-separated segment ids")
        # Not a flag a person types: two commands rather than `--lift`, because a
        # verb command is named for what it does and `lx hold --lift` reads as the
        # opposite of what it would do. One handler behind both, so the pair
        # cannot drift.
        h.set_defaults(fn=cmd_hold, lift=lift)

    for name, lift, blurb in (
            ("waive", False, "stand by this wording: report the rules a reviewer "
                             "can overrule at warn instead of failing the build"),
            ("unwaive", True, "put a waived segment's errors back")):
        w = sub.add_parser(name, help=blurb)
        w.add_argument("src")
        w.add_argument("--lang", required=True)
        w.add_argument("--ids", required=True, help="comma-separated segment ids")
        # The pair `hold`/`unhold` follows, for its reasons: a verb command says
        # what it does where a `--lift` flag reads as its opposite, and one
        # handler behind both is what keeps them from drifting.
        w.set_defaults(fn=cmd_waive, lift=lift)

    c = sub.add_parser("check", help="validate; exit 1 on error")
    c.add_argument("src")
    c.add_argument("--lang", required=True)
    c.add_argument("--json", action="store_true")
    c.add_argument("--max", type=int, default=25)
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("render", help="rebuild the target document")
    r.add_argument("src")
    r.add_argument("--lang", required=True)
    r.add_argument("-o", "--out", help="'-' for stdout; default from output_pattern")
    r.add_argument("--fallback", action="store_true",
                   help="untranslated segments fall back to source")
    r.set_defaults(fn=cmd_render)

    # Beside `render` and sharing its spellings, because it answers the same
    # question — what does this document say once it is rebuilt — and differs only
    # in whether the answer is joined. It writes nothing, so it has no `--out`.
    bl = sub.add_parser("blocks", help="the rendered document, block by block")
    bl.add_argument("src")
    bl.add_argument("--lang", required=True)
    bl.add_argument("--fallback", action="store_true",
                    help="untranslated segments fall back to source")
    bl.add_argument("--json", action="store_true")
    bl.set_defaults(fn=cmd_blocks)

    sn = sub.add_parser("sentences", help="how a segment's text divides into sentences")
    sn.add_argument("src")
    sn.add_argument("--lang", required=True)
    sn.add_argument("--ids", help="comma-separated segment ids; default every segment")
    sn.add_argument("--source", action="store_true",
                    help="split the masked source instead of the target")
    sn.add_argument("--json", action="store_true")
    sn.set_defaults(fn=cmd_sentences)

    m = sub.add_parser("commit", help="bank approved segments in the translation memory")
    m.add_argument("src")
    m.add_argument("--lang", required=True)
    m.set_defaults(fn=cmd_commit)

    s_ = sub.add_parser("stats", help="coverage across tracked documents")
    s_.add_argument("--lang")
    s_.set_defaults(fn=cmd_stats)

    st = sub.add_parser("status", help="the machine-readable project status contract")
    st.add_argument("--json", action="store_true")
    st.add_argument("--lang", help="report only this target language")
    st.add_argument("--scan", metavar="ROOT",
                    help="report every project under ROOT instead of the current directory")
    st.add_argument("--depth", type=int, default=SCAN_DEPTH,
                    help=f"how deep under --scan a project may be found (default {SCAN_DEPTH})")
    st.set_defaults(fn=cmd_status)

    un = sub.add_parser("untracked", help="files matching `sources` with no state yet")
    un.add_argument("--json", action="store_true")
    # The same flag and the same default as `lx check`, deliberately: one number
    # to remember across the two commands that truncate. *Lost:* a larger default
    # chosen from a guess at how many files a project has, which buys one
    # keystroke and costs a second number.
    un.add_argument("--max", type=int, default=25,
                    help="rows to print; the rest are counted. --json is never truncated")
    un.set_defaults(fn=cmd_untracked)

    tr = sub.add_parser("translate", help="translate segments with a configured model")
    tr.add_argument("src")
    tr.add_argument("--lang", required=True)
    tr.add_argument("--mode", choices=list(ROUTING_STAGES), default="draft")
    tr.add_argument("--ids", help="comma-separated segment ids")
    tr.add_argument("--all", action="store_true", help="include already-translated segments")
    tr.add_argument("--limit", type=int, default=0, help=_LIMIT_HELP)
    _add_llm_flags(tr)
    tr.set_defaults(fn=cmd_translate)

    rp = sub.add_parser("repair", help="re-translate only segments failing check")
    rp.add_argument("src")
    rp.add_argument("--lang", required=True)
    rp.add_argument("--limit", type=int, default=0, help=_LIMIT_HELP)
    _add_llm_flags(rp)
    rp.set_defaults(fn=cmd_repair)

    rn = sub.add_parser("run", help="extract, translate, check, repair, render")
    rn.add_argument("src")
    rn.add_argument("--lang", required=True)
    rn.add_argument("--tone", help="register for this document; see `lx extract --help`")
    rn.add_argument("-o", "--out")
    rn.add_argument("--polish", action="store_true", help="second pass for fluency")
    rn.add_argument("--max-rounds", type=int, default=None)
    rn.add_argument("--limit", type=int, default=0,
                    help="most segments each model pass sends; 0 for all of them. "
                         "The repair rounds only revisit what this run itself sent, "
                         "and the document is not rendered while segments remain "
                         "untranslated — run the same command again to continue")
    rn.add_argument("--force", action="store_true", help="render even if errors remain")
    _add_llm_flags(rn)
    rn.set_defaults(fn=cmd_run)

    w = sub.add_parser("web", help="open the review workbench")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8787)
    w.add_argument("--no-browser", action="store_true")
    w.set_defaults(fn=cmd_web)

    return p


def main(argv=None):
    # Before parse_args, because argparse writes usage and errors to these two.
    force_utf8(sys.stdout)
    force_utf8(sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        # Inside the `try`, not before it. Every command reads the configuration
        # first, so a typo in `lx.config.json` was answered by all twenty of them
        # with a traceback and exit 1 — where every other refusal in this CLI is
        # one sentence and exit 2. `load_config` raises `ConfigError` now and
        # this is the half that catches it. Found 2026-08-19 by the mutation pass
        # over `lx status`, whose contract enumerates exit 0 and exit 2 and no
        # third thing.
        cfg = load_config(args.config)
        args.fn(args, cfg)
    # `ProviderError` joined this tuple on 2026-08-20, with `lx models` — the
    # first command whose whole job is a call to a backend, so the first one that
    # can fail with nothing translated and nothing to report but the failure.
    # Every other command reaches a provider through `translate.run_batch` or
    # `retry_one`, which swallow it into a per-segment reason; the two paths that
    # never did are `providers.build` refusing an unknown provider name, and this
    # command. Both answered a traceback and exit 1 where every other refusal in
    # this CLI is one sentence and exit 2.
    except (FileNotFoundError, StateVersionError, UnsupportedSource,
            GlossaryWriteError, UnknownFormat, UndecodableDocument,
            StyleSheetError, ConfigError, UnusableTarget, UnnamedRegister,
            ProviderError) as e:
        print(f"lx: {e}", file=sys.stderr)
        sys.exit(2)
    except BrokenPipeError:
        os._exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
