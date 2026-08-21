"""Where one sentence ends and the next begins.

The rule lives here, in Python, and nowhere else. A reading view that judges
whether prose *flows* has to be able to point at a sentence, and a boundary rule
invented in the browser would be a second rule that ``lx``, an agent and CI
cannot see — which makes a sentence-level diff impossible outside the frontend.
So the frontend renders what it is given and computes nothing; the cost of that
choice is that the highlight is stale while a reviewer types and is recomputed on
debounce or blur, which is designed in rather than argued away.

**Sentences are returned as text, never as offsets.** The same argument the block
map won with: a CRLF document shifts every offset because the terminator is
re-imposed at render, and Python counts code points where JavaScript counts
UTF-16 code units, so a name outside the BMP — routine in Chinese — desynchronizes
the two silently. Offsets in UTF-16 code units and offsets in code points were
both put to the maintainer on 2026-08-17 and both refused. See
``docs/decisions.md``, 2026-08-21.

**The partition is exact.** ``"".join(split(text)) == text`` for every input, so a
client walks the block's text with a cursor instead of searching it. That matters
because two sentences in one paragraph may be byte-identical, and a client
locating them by search would put both highlights on the first one.

**This is not a validator.** Invariant 4 admits a rule to ``checks.py`` only when
a program can decide it without judgement, and a sentence boundary is not such a
rule — the module owns its failures rather than pretending it has none, and they
are written down in :data:`KNOWN_FAILURES` and in the contract. Nothing here
reaches ``store.SEGMENTATION_VERSION``, the translation-memory key or
``checks.py``; the first two would invalidate every banked wording.

**Why these tables are not ``cli._SENTENCE_END`` and ``cli._OPEN_QUOTES``.**
Those answer a different question in a different language: ``lx terms`` reads the
*English source* to decide whether a capitalized token stands at a sentence
start, so its terminator set is ``".!?…"`` with no full-width mark in it at all.
Merging the two would widen that command's rule to punctuation its input never
contains. The difference is recorded rather than reconciled, which is the same
call ``mask._TRANSLATABLE_RE`` already makes against ``cli._LETTER``. The one
thing that genuinely is one answer to one question — the abbreviation list — is
shared: it is ``cfg["terms"]["abbreviations"]`` and :func:`cli.do_sentences`
passes it in.
"""

import re

from .mask import PH_RE

__all__ = ["KNOWN_FAILURES", "split"]

#: A sentence really is over. Full-width first, because the target language is
#: Traditional Chinese and this module is read while thinking about it.
_STRONG = "。！？.!?"

#: Trailing off. A run of these ends a sentence far less often than a full stop
#: does — ``一、二、三……十。`` is one sentence and ``「別怕……」然後熄了燈。`` is
#: another — so they only take a boundary where something after them says so.
_WEAK = "…⋯"

#: Pulled in after a terminator run, so the mark stays with the sentence it
#: closes. Every one of these is a *closing* glyph with a different opening
#: twin — ``」`` against ``「``, ``”`` against ``“`` — so finding one after a full
#: stop settles what it is doing there, and it is absorbed with no further test.
_CLOSERS = "’”»›」』〉》】〕｝）)]}"

#: The marks that open exactly as often as they close, because the glyph is the
#: same at both ends. ``*``, ``_`` and ``~`` are here because invariant 3 records
#: emphasis as reaching a segment unmasked; without them ``*She never returned.*``
#: ends one character early.
#:
#: **They are absorbed only when a run of them is followed by whitespace or by
#: the end of the text**, which is what tells a closing mark from an opening one
#: without any table — and the run has to be taken whole, or ``**She stayed.**``
#: ends after the first asterisk of the pair. English escaped the question by
#: accident until 2026-08-21: a space follows the stop, so the absorbing loop
#: never started. Traditional Chinese writes no space after ``。``, so it always
#: bit — ``他走了。**她留下。**`` came back as ``他走了。**`` and ``她留下。**``,
#: every piece carrying a stray delimiter and the emphasis pair broken across the
#: boundary.
_SYMMETRIC = "\"'*_~"

#: What may sit between a full stop and the whitespace that follows it. The union,
#: deliberately: :func:`_stop_is_terminal` asks a different question — *is there
#: a space after this stop at all* — and for that question an opening asterisk
#: and a closing one are the same character in the same place. Narrowing it to
#: :data:`_CLOSERS` merges ``*She left.* He stayed.`` into one piece.
_TRAILING = _CLOSERS + _SYMMETRIC

#: A mark that cannot begin a line in Chinese typesetting, so a boundary in front
#: of one is a boundary in the wrong place. ``他嘴裡念著「快跑！」，腳下卻沒動。``
#: is the case: without this the reading view highlights a sentence starting at a
#: full-width comma, which is visibly wrong in the target language on a routine
#: construction.
#:
#: Quote-shaped marks are deliberately **absent**, even the closing ones: ``”``
#: and ``’`` open as often as they close, and ``He left. ’Tis done.`` is two
#: sentences.
_CANNOT_START = "，、；：,;:」』〉》】〕｝）)]}"

#: What a weak run needs in front of it before it may end a sentence.
_OPENING = "「『（《〈【〔｛“‘\"'([{"

_TERMINATOR_RE = re.compile(f"[{re.escape(_STRONG + _WEAK)}]+")

#: A maximal run of adjacent placeholders, treated as one atom. ``mask.PH_RE`` is
#: reused rather than respelled — ``translate.mentions`` records that three copies
#: of a matching rule had happened here before anybody noticed.
_ATOM_RE = re.compile(f"(?:{PH_RE.pattern})+")

#: A letter, in any script. Spelled as "a word character that is neither a digit
#: nor an underscore" rather than as a range, because a range here would be a
#: fourth copy of one ``mask.CJK`` already owns and would still be wrong for kana,
#: Hangul and every astral-plane ideograph.
_LETTER = r"[^\W\d_]"

#: The two marks English writes *inside* a word. Named rather than spelled twice,
#: because :data:`_WORD_RE` and the walk that finds where it could start have to
#: agree about this set exactly — the walk exists to bound the pattern's search,
#: and a walk that stopped one character earlier than the pattern accepts would
#: silently shorten the token.
_INWORD = "'’-"

#: The word in front of a full stop: letters, plus the two marks English writes
#: *inside* a word. It exists so an abbreviation carrying one of them — ``Int'l``,
#: a hyphenated place name — is recognized as the token it is; walking back over
#: ``str.isalnum`` stops at the apostrophe and offers ``l``, which matches no
#: entry in any list.
#:
#: **It is not what protects a contraction**, and saying so is the point of this
#: note: ``He didn't.`` is safe because the initials rule below requires the lone
#: letter to be an *upper-case* one, so ``t`` is not an initial whichever spelling
#: found it. Two of the three designs this rule was chosen from suppressed any
#: lone letter, merged every contraction-final sentence with the one after it, and
#: neither admitted the failure — which is the reason the initials rule is written
#: the way it is. The first version of this pattern silently excluded the very
#: characters this docstring said it included, and the module's tests could not
#: see the difference; a mutation pass on 2026-08-21 could.
_WORD_RE = re.compile(
    rf"{_LETTER}(?:(?:{_LETTER}|[{re.escape(_INWORD)}])*{_LETTER})?\Z")

#: :data:`_LETTER` as a compiled pattern, so one position can be asked about
#: without slicing the string that holds it. That is the whole of what
#: :func:`_word_before` needs and the whole of why it is linear.
_LETTER_RE = re.compile(_LETTER)

#: Cases this rule gets wrong, on purpose or for want of a rule that would cost
#: more than it buys. Stated here as well as in the contract because a splitter
#: that claims no failures is not being honest, and the next reader deserves the
#: list before they discover it.
KNOWN_FAILURES = (
    "An abbreviation that genuinely ends a sentence keeps the sentence open: "
    "'He turned onto Main St. Then he stopped.' is one sentence, because `St` is "
    "in the abbreviation list.",
    "A sentence-final lower-case word opener merges: 'The bell rang. iPhone "
    "screens lit up.' is one sentence.",
    "An enumerated run reads as sentences: '1. First item. 2. Second item.' is "
    "four pieces.",
    "In rendered text a full stop followed by a space *inside* restored markup — "
    "a URL or a code span — can take a boundary, because this rule sees text and "
    "not slots. Masked text does not have the problem: a placeholder is an atom.",
    "Chinese dialogue attribution over-splits: '「站住！」他喊。沒有人停下。' is "
    "three pieces, and '他喊。' is half a sentence. English is protected by "
    "`str.islower` on the word after a strong run and Chinese cannot be, because "
    "`islower` is False for every Chinese character. Telling an attribution verb "
    "from an ordinary one needs a verb table, which is judgement — invariant 4 — "
    "so this is admitted rather than repaired. The *comma* form is already right: "
    "'他嘴裡念著「快跑！」，腳下卻沒動。' stays whole, because `，` cannot begin a "
    "line.",
)


def split(text, abbreviations=()):
    """``text`` cut into sentences, in order, concatenating back to ``text``.

    ``abbreviations`` is a collection of words whose full stop does not end a
    sentence, spelled without the stop — ``config``'s ``terms.abbreviations``.

    ``split("")`` is ``[]``; every other input yields at least one element, and
    text with no boundary in it yields exactly one. Whitespace after a boundary
    belongs to the sentence that ended, which is what makes the concatenation
    exact without anybody having to decide where a run of blanks "really" goes.
    """
    if not text:
        return []
    abbreviations = {str(a) for a in abbreviations}
    out, start, i, n = [], 0, 0, len(text)
    while i < n:
        # A placeholder run is stepped over whole. **This is not what makes the
        # atom property true today** — no character in `⟦\d+⟧` is a terminator, so
        # a boundary could not land inside one even without this, and a mutation
        # pass on 2026-08-21 confirmed the suite cannot tell the two versions
        # apart. It is kept because the property the contract promises is about
        # placeholders and not about which characters happen to be terminators
        # this month: widen `_STRONG` or `_WEAK` by one character and this line is
        # the difference between a promise and a coincidence.
        atom = _ATOM_RE.match(text, i)
        if atom:
            i = atom.end()
            continue
        run = _TERMINATOR_RE.match(text, i)
        if not run:
            i += 1
            continue
        end = _boundary_end(text, run, abbreviations)
        if end is None:
            i = run.end()
            continue
        while end < n and text[end].isspace():
            end += 1
        out.append(text[start:end])
        start = i = end
    if start < n:
        out.append(text[start:])
    return out


def _boundary_end(text, run, abbreviations):
    """Where this terminator run's sentence ends, or ``None`` if it does not.

    The returned offset is before any trailing whitespace; :func:`split` absorbs
    that separately, so the two questions — *is this a boundary* and *how much
    blank space goes with it* — stay apart.
    """
    marks, n = run.group(0), len(text)
    stop = run.end()
    if not any(ch in _STRONG for ch in marks):
        strong = False                          # a run of … or ⋯ alone
    elif marks != ".":
        strong = True                           # ！？。 or a run such as ?! or ...
    elif _stop_is_terminal(text, run.start(), abbreviations):
        strong = True
    else:
        return None                             # 3.14, example.com, Mr. Darcy

    while stop < n and text[stop] in _CLOSERS:
        stop += 1
    # A run of symmetric marks goes with the sentence that ended only if it is
    # closing one, and what follows the *whole run* is the only thing that says
    # so. Per character it would take `**` apart and end `**She stayed.**` after
    # the first asterisk; per run it either belongs to this sentence or to the
    # next, which is what the pair being a pair means.
    pair = stop
    while pair < n and text[pair] in _SYMMETRIC:
        pair += 1
    if pair > stop and (pair >= n or text[pair].isspace()):
        stop = pair
    # A placeholder run glued to the closing marks belongs to the sentence that
    # ended: `He left.⟦3⟧ She stayed.` is a closing tag, not an opening one. The
    # slots would say which, and this rule is given text rather than slots — so
    # the choice is stated here rather than guessed per call.
    atom = _ATOM_RE.match(text, stop)
    if atom:
        stop = atom.end()

    ahead = stop
    while ahead < n and text[ahead].isspace():
        ahead += 1
    if ahead >= n:
        return stop
    nxt = text[ahead]
    if nxt in _CANNOT_START:
        return None
    if strong:
        return None if nxt.islower() else stop
    # A weak run needs a capital, an opening mark or the end of the text in front
    # of it. `str.isupper` is false for every Chinese character, and that is the
    # rule rather than a limitation of it: `一、二、三……十。` and
    # `「我不知道……」她輕聲說。` are each one sentence, so an ellipsis followed by
    # more Chinese continues what it was in the middle of. Under-splitting is the
    # recoverable direction — a reviewer clicking two sentences at once is a
    # smaller failure than one clicking half of one.
    return stop if nxt in _OPENING or nxt.isupper() else None


def _stop_is_terminal(text, at, abbreviations):
    """Whether a lone ASCII full stop at ``at`` is a terminator at all.

    Two tests and they are independent. On the right, the stop must be followed
    by whitespace or by the end of the text — which is the whole of what keeps
    ``3.14`` and ``example.com`` in one piece, and it is load-bearing because
    neither is masked: ``mask.INLINE_PATTERNS`` has no bare-domain pattern and
    its ``url`` entry requires a scheme. HANDOFF-028 assumed masking had removed
    both; it had not.

    On the left, the word in front of the stop must not be a configured
    abbreviation and must not be a lone capital, which is what keeps
    ``J. R. R. Tolkien`` and ``U.S.`` one token each.

    **The word is read with** :data:`_WORD_RE`, **never by walking back over
    ``str.isalnum``.** That spelling stops at the apostrophe, so the token in
    front of the stop in ``He didn't.`` is ``t`` — a lone capital's lower-case
    twin, near enough that two of the three designs this rule was chosen from
    merged every contraction-final sentence with the one after it, and neither
    admitted it. Measured 2026-08-21.
    """
    after = at + 1
    while after < len(text) and text[after] in _TRAILING:
        after += 1
    atom = _ATOM_RE.match(text, after)
    if atom:
        after = atom.end()
    if after < len(text) and not text[after].isspace():
        return False
    token = _word_before(text, at)
    if token is None:
        return True
    if token in abbreviations:
        return False
    # A lone capital is an initial. `It was I.` and `Grade A.` are the two
    # English words this loses and they are the accepted cost; a lower-case lone
    # letter is left alone, because nothing writes one at the end of a sentence
    # and suppressing it would buy nothing.
    return not (len(token) == 1 and token.isupper())


def _word_before(text, at):
    """The token :data:`_WORD_RE` matches ending at ``at``, or ``None``.

    **This is** :data:`_WORD_RE` **applied to a bounded window, and the bound is
    the whole point.** The obvious spelling, ``_WORD_RE.search(text[:at])``, is
    quadratic in the input: it copies the entire prefix at every candidate full
    stop, and English prose offers one full stop per sentence. Measured on this
    module before the change — 2 KB 0.013 s, 8 KB 0.200 s, 32 KB 3.198 s, 64 KB
    12.387 s, four times the input for sixteen times the time — against a
    ``POST /api/sentences`` that has no request size limit and a chapter-sized
    segment that is ordinary input here.

    ``_WORD_RE.search(text, 0, at)`` does **not** fix it, and that is worth
    saying because it is what one reaches for first: dropping the copy leaves
    ``search`` trying every start position from 0, so the scan stays quadratic
    and only the constant improves.

    What fixes it is knowing where the pattern *could* start. Every character
    the pattern accepts is a letter or a member of :data:`_INWORD`, so the match
    cannot begin before the run of those that ends at ``at`` — walk back over
    that run and hand ``search`` the window. The run is a word, so this is
    O(word) per stop and O(text) over the whole input.

    The leading guard is not an optimization of the common case but of the
    pathological one. The pattern's last atom is a letter, so a stop whose left
    neighbour is not one cannot match at all; without the early return,
    ``search`` would try — and fail — from every letter in the window, which is
    O(word²) on input such as ``a-a-a-…-.``. With it, the first letter in the
    window matches and every position before it fails on its first character.
    """
    if at <= 0 or not _LETTER_RE.match(text, at - 1):
        return None
    lo = at
    while lo and (_LETTER_RE.match(text, lo - 1) or text[lo - 1] in _INWORD):
        lo -= 1
    word = _WORD_RE.search(text, lo, at)
    return word.group(0) if word else None
