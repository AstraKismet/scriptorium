"""Command line interface. Every other surface — the skill, the web UI, CI — is
a caller of these functions, so behaviour cannot diverge between them."""

import argparse
import json
import os
import re
import sys
from collections import Counter

from . import __version__, formats
from .checks import check_segment
from .config import (
    DEFAULT_TONE,
    GLOSSARY_HEADER,
    canonical_tone,
    dump_json,
    load_config,
    load_dnt,
    load_glossary,
    write_templates,
)
from .docio import (
    UndecodableDocument,
    apply_terminator,
    read_document,
    split_terminator,
    write_document,
    write_document_to_stdout,
)
from .formats import UnknownFormat
from .mask import repair_placeholders
from .normalize import normalize, polish_rendered
from .store import (
    StateVersionError,
    append_tm,
    db_path,
    load_doc,
    load_tm,
    prior_doc,
    prior_targets,
    report_path,
    save_doc,
    save_segments,
    save_targets,
    segment_key,
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


class UnsupportedSource(ValueError):
    """A command that only works on one kind of source was given another.

    Raised rather than answered, and caught in :func:`main` for exit 2, because
    the alternative for `lx terms` on a non-English document is a list of quiet
    nonsense — Chinese and Japanese have no capitalization for the rule to read,
    so it would propose nothing and report success.
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

def do_extract(src, lang, cfg, tone=None, reset=False):
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
    nodes, segments = fmt.parse(text, load_dnt(cfg), opts)

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
    prior = {} if reset else prior_targets(src, lang)

    tm = load_tm(lang)
    reused, rejected = 0, 0
    for seg in segments:
        # This document's own state first, then the memory. Both are proposals,
        # not results: reuse goes through `accept` for the same reason model
        # output does — the placeholder set is the one thing neither the pipeline
        # nor a reviewer can reconstruct, and a stale entry that keeps its key
        # while the mask configuration moves under it is the measured case.
        candidates = []
        key = segment_key(seg, tone)
        if key in prior:
            candidates.append(prior[key])
        hit, hit_origin = tm_lookup(tm, seg, tone)
        if hit is not None:
            candidates.append((hit, hit_origin))
        for proposal, origin in candidates:
            # The memory is tried even when this document's own target was
            # refused: the two can differ, and a good banked wording should not be
            # lost to a stale one sitting in front of it.
            target, _why = accept(seg, proposal, lang, cfg)
            if target is not None:
                seg["target"], seg["origin"], seg["status"] = target, origin, "translated"
                reused += 1
                break
        else:
            if candidates:
                rejected += 1

    doc = {
        "version": __version__, "source": os.path.relpath(src), "lang": lang,
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
    return doc, reused, rejected


def cmd_extract(args, cfg):
    doc, reused, rejected = do_extract(args.src, args.lang, cfg, args.tone, args.reset)
    pending = sum(1 for s in doc["segments"] if s["status"] == "pending")
    _out(f"{args.src} [{args.lang}] -> {db_path()}")
    line = f"  segments {len(doc['segments'])} | reused {reused} | pending {pending}"
    # Only when it happened, and named as the memory's problem rather than the
    # document's: a rejected reuse means a banked entry no longer fits the
    # segment it matched, and the segment went back to pending because of it.
    if rejected:
        line += f" | {rejected} stale memory hit(s) refused"
    # Likewise only when it is not the default: the register decides both the
    # brief and which half of the memory answers, so a document that is in one
    # should say so on the line that reports what carried over.
    if canonical_tone(doc["tone"]) != DEFAULT_TONE:
        line += f" | tone {doc['tone']}"
    _out(line)
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

def pending_segments(doc, include_all=False, limit=0):
    out = [s for s in doc["segments"] if include_all or s["status"] == "pending"]
    return out[:limit] if limit else out


def cmd_todo(args, cfg):
    doc = load_doc(args.src, args.lang)
    glossary = load_glossary(cfg)
    items = []
    for seg in pending_segments(doc, args.all, args.limit):
        item = {"id": seg["id"], "kind": seg["kind"], "text": seg["masked"]}
        low = seg["masked"].lower()
        # A row with no target is a candidate `lx terms` proposed and nobody has
        # decided yet. Handing an agent `{"term": "Ashcombe", "use": ""}` asks it
        # to render the name as nothing, so an unfinished row stays silent —
        # which is what `checks.check_segment` already does with one.
        hints = [{"term": r["source"], "use": r["target"]} for r in glossary
                 if r["target"]
                 and re.search(rf"(?<![A-Za-z]){re.escape(r['source'].lower())}(?![A-Za-z])", low)]
        if hints:
            item["glossary"] = hints
        if seg.get("issues"):
            item["fix"] = seg["issues"]
        items.append(item)
    _out(json.dumps({
        "source": doc["source"], "lang": doc["lang"], "tone": doc["tone"],
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
    `glossary`, `dnt` and `output_pattern` are confined at use time or they are
    not writable over HTTP. Recorded in `docs/decisions.md`, 2026-08-02,
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

def do_apply(src, lang, cfg, incoming, origin="agent"):
    doc = load_doc(src, lang)
    by_id = {s["id"]: s for s in doc["segments"]}
    applied, unknown, changed = 0, [], []
    for sid, text in incoming.items():
        seg = by_id.get(sid)
        if not seg:
            unknown.append(sid)
            continue
        seg["target"] = normalize(repair_placeholders(text), lang, cfg)
        seg["status"], seg["origin"] = "translated", origin
        seg.pop("issues", None)
        changed.append(seg)
        applied += 1
    # The segments this call touched, not the whole document: apply is what the
    # workbench calls on every keystroke-sized save, and rewriting a novel's
    # skeleton to record one edited paragraph is the amplification the state
    # layer moved to SQLite to remove.
    save_segments(src, lang, changed)
    return applied, unknown


def cmd_apply(args, cfg):
    raw = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    data = json.loads(raw)
    if isinstance(data, dict) and "segments" in data:
        data = data["segments"]
    incoming = ({d["id"]: d.get("text", d.get("target", "")) for d in data}
                if isinstance(data, list) else dict(data))
    applied, unknown = do_apply(args.src, args.lang, cfg, incoming, args.origin)
    _out(f"applied {applied} segment(s)" + (f"; unknown ids ignored: {unknown}" if unknown else ""))


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
        # Only the segments, never the skeleton: check reads the document and
        # writes back one field of each segment, and the nodes it would rewrite
        # are the largest thing in the state.
        save_segments(src, lang, doc["segments"])
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

def do_render(src, lang, cfg, fallback=False):
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


def default_output(src, lang, cfg):
    pattern = cfg.get("output_pattern", "i18n/{lang}/{path}")
    return pattern.format(lang=lang, path=os.path.relpath(src).replace(os.sep, "/"),
                          name=os.path.basename(src))


def cmd_render(args, cfg):
    text, missing = do_render(args.src, args.lang, cfg, args.fallback)
    if args.out == "-":
        write_document_to_stdout(text)
        return
    out = args.out or default_output(args.src, args.lang, cfg)
    write_document(out, text)
    _out(f"wrote {out}" + (f" ({missing} untranslated)" if missing else ""))


def cmd_commit(args, cfg):
    doc = load_doc(args.src, args.lang)
    n = append_tm(args.lang, tm_records(doc, load_tm(args.lang)))
    _out(f"translation memory += {n} entries")


def cmd_stats(args, cfg):
    docs = tracked(args.lang)
    if not docs:
        _out("nothing tracked yet — run `lx extract`")
        return
    for doc in docs:
        total = len(doc["segments"])
        done = sum(1 for s in doc["segments"] if s.get("target"))
        pct = done * 100 // max(total, 1)
        _out(f"{pct:3d}% [{'#' * (pct // 5):<20}] {done}/{total}  {doc['source']} [{doc['lang']}]")


def cmd_init(args, cfg):
    created = write_templates()
    _out("initialized" + (f": {', '.join(created)}" if created else " (already set up)"))


# ── translate / repair / run ───────────────────────────────────────────────

def cmd_providers(args, cfg):
    from .providers import available
    for p in available(cfg):
        key = "no key needed" if not p["needs_key"] else (
            f"{p['key_env']} set" if p["key_present"] else f"{p['key_env']} MISSING")
        _out(f"{p['name']:12} {p['kind']:10} {p['model']:28} {p['base_url']:34} {key}")
    routing = cfg.get("routing", {})
    _out("\nrouting: " + "  ".join(f"{k}={v}" for k, v in routing.items()))


def _translate(src, lang, cfg, segments, mode, args):
    from .translate import Progress, translate_segments
    doc = load_doc(src, lang)
    if not segments:
        _out("nothing to do")
        return 0, []
    if args.dry_run:
        chars = sum(len(s["masked"]) for s in segments)
        _out(f"dry run: {len(segments)} segment(s), {chars} source characters, "
             f"mode={mode}, provider={args.provider or cfg.get('routing', {}).get(mode)}")
        return 0, []
    origin = f"llm:{mode}"
    # Each batch is committed as it lands. `do_apply` still runs at the end and
    # is still the authority on what was applied — it normalizes and reports
    # unknown ids — but it is no longer the *first* time anything reaches disk,
    # which is what an interrupted novel depends on. The two writes agree:
    # `accept` has already normalized the text this one stores.
    results, failures = translate_segments(
        segments, doc, cfg, provider_name=args.provider, mode=mode,
        batch_size=args.batch, concurrency=args.concurrency,
        progress=Progress(_out),
        on_batch=lambda ok: save_targets(src, lang, ok, origin))
    applied, _ = do_apply(src, lang, cfg, results, origin=origin)
    for sid, why in failures:
        _out(f"  unresolved {sid}: {why}")
    return applied, failures


def cmd_translate(args, cfg):
    doc = load_doc(args.src, args.lang)
    if args.mode == "polish":
        segments = [s for s in doc["segments"] if s.get("target") and s["kind"] in ("para", "quote", "list")]
    elif args.ids:
        wanted = set(args.ids.split(","))
        segments = [s for s in doc["segments"] if s["id"] in wanted]
    else:
        segments = pending_segments(doc, include_all=args.all, limit=args.limit)
    applied, failures = _translate(args.src, args.lang, cfg, segments, args.mode, args)
    _out(f"translated {applied} segment(s)" + (f", {len(failures)} unresolved" if failures else ""))


def cmd_repair(args, cfg):
    from .translate import failing_segments
    do_check(args.src, args.lang, cfg)
    doc = load_doc(args.src, args.lang)
    segments = failing_segments(doc, cfg)
    if not segments:
        _out("nothing failing")
        return
    _out(f"repairing {len(segments)} failing segment(s)")
    _translate(args.src, args.lang, cfg, segments, "repair", args)
    report, _ = do_check(args.src, args.lang, cfg)
    _out(f"after repair: {report['errors']} error(s), {report['warnings']} warning(s)")


def cmd_run(args, cfg):
    """extract → translate → check → repair* → render, in one command."""
    doc, reused, rejected = do_extract(args.src, args.lang, cfg, args.tone)
    pending = [s for s in doc["segments"] if s["status"] == "pending"]
    _out(f"{args.src} [{args.lang}] · {len(doc['segments'])} segments · "
         f"{reused} reused · {len(pending)} to translate"
         + (f" · {rejected} stale memory hit(s) refused" if rejected else ""))

    if pending:
        _translate(args.src, args.lang, cfg, pending, "draft", args)
    if args.polish:
        doc = load_doc(args.src, args.lang)
        prose = [s for s in doc["segments"]
                 if s.get("target") and s["kind"] in ("para", "quote", "list")]
        _out(f"polishing {len(prose)} prose segment(s)")
        _translate(args.src, args.lang, cfg, prose, "polish", args)

    from .translate import failing_segments
    rounds = args.max_rounds if args.max_rounds is not None else cfg.get("batch", {}).get("max_repair_rounds", 3)
    previous = None
    for attempt in range(rounds):
        report, _ = do_check(args.src, args.lang, cfg)
        if not report["errors"]:
            break
        bad = failing_segments(load_doc(args.src, args.lang), cfg)
        signature = {s["id"]: s.get("target") for s in bad}
        if signature == previous:
            _out("repair made no difference last round; stopping so it does not spin")
            break
        previous = signature
        _out(f"repair round {attempt + 1}/{rounds}: {len(bad)} failing segment(s)")
        _translate(args.src, args.lang, cfg, bad, "repair", args)

    report, _ = do_check(args.src, args.lang, cfg)
    _out(f"check: {report['errors']} error(s), {report['warnings']} warning(s)")
    if report["errors"] and not args.force:
        _out("not rendering while errors remain — inspect with `lx check` or fix in `lx web`, "
             "or pass --force to render anyway")
        sys.exit(1)
    out = args.out or default_output(args.src, args.lang, cfg)
    text, missing = do_render(args.src, args.lang, cfg, fallback=args.force)
    write_document(out, text)
    _out(f"wrote {out}")
    _out("review the rendered file, then `lx commit` to bank the wording in the translation memory")


def cmd_web(args, cfg):
    from .web.server import serve
    serve(host=args.host, port=args.port, open_browser=not args.no_browser)


# ── parser ─────────────────────────────────────────────────────────────────

def _add_llm_flags(p):
    p.add_argument("--provider", help="provider name from lx.config.json; overrides routing")
    p.add_argument("--batch", type=int, help="segments per request")
    p.add_argument("--concurrency", type=int, help="parallel requests")
    p.add_argument("--dry-run", action="store_true", help="report the work without calling a model")


def build_parser():
    p = argparse.ArgumentParser(prog="lx", description="Scriptorium localization pipeline")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", default="lx.config.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="scaffold config and state").set_defaults(fn=cmd_init)
    sub.add_parser("providers", help="list configured backends").set_defaults(fn=cmd_providers)

    e = sub.add_parser("extract", help="parse a document into segments")
    e.add_argument("src")
    e.add_argument("--lang", required=True)
    e.add_argument("--tone", help="register for this document: technical (default) or "
                                  "literary. Frozen on the document and kept by later "
                                  "extracts; changing it leaves the existing translations "
                                  "behind, so run `lx commit` first")
    e.add_argument("--reset", action="store_true",
                   help="discard the existing state instead of carrying its translations "
                        "over. It does not read the old file at all, so the register goes "
                        "back to --tone or config with everything else")
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
    a.set_defaults(fn=cmd_apply)

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

    m = sub.add_parser("commit", help="bank approved segments in the translation memory")
    m.add_argument("src")
    m.add_argument("--lang", required=True)
    m.set_defaults(fn=cmd_commit)

    s_ = sub.add_parser("stats", help="coverage across tracked documents")
    s_.add_argument("--lang")
    s_.set_defaults(fn=cmd_stats)

    tr = sub.add_parser("translate", help="translate segments with a configured model")
    tr.add_argument("src")
    tr.add_argument("--lang", required=True)
    tr.add_argument("--mode", choices=["draft", "polish", "repair"], default="draft")
    tr.add_argument("--ids", help="comma-separated segment ids")
    tr.add_argument("--all", action="store_true", help="include already-translated segments")
    tr.add_argument("--limit", type=int, default=0)
    _add_llm_flags(tr)
    tr.set_defaults(fn=cmd_translate)

    rp = sub.add_parser("repair", help="re-translate only segments failing check")
    rp.add_argument("src")
    rp.add_argument("--lang", required=True)
    _add_llm_flags(rp)
    rp.set_defaults(fn=cmd_repair)

    rn = sub.add_parser("run", help="extract, translate, check, repair, render")
    rn.add_argument("src")
    rn.add_argument("--lang", required=True)
    rn.add_argument("--tone", help="register for this document; see `lx extract --help`")
    rn.add_argument("-o", "--out")
    rn.add_argument("--polish", action="store_true", help="second pass for fluency")
    rn.add_argument("--max-rounds", type=int, default=None)
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
    cfg = load_config(args.config)
    try:
        args.fn(args, cfg)
    except (FileNotFoundError, StateVersionError, UnsupportedSource,
            GlossaryWriteError, UnknownFormat, UndecodableDocument) as e:
        print(f"lx: {e}", file=sys.stderr)
        sys.exit(2)
    except BrokenPipeError:
        os._exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
