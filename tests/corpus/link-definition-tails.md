Whether a line is a link reference definition is decided by the whole line and
not by `[label]:` alone. Both halves are here because only holding both makes the
rule visible: a repair that swallows the second half passes every assertion about
the first.

The half that is a definition. Every line stays in the skeleton, because
translating a label breaks every reference to it.

[plain]: https://example.invalid/a
[titled]: https://example.invalid/b "With a double-quoted title"
[single]: https://example.invalid/c 'and a single-quoted one'
[parend]: https://example.invalid/d (and a parenthesized one)
[angled]: </a destination with a space> "which only the angle form allows"
[balanced]: https://example.invalid/e(1)
[escaped]: https://example.invalid/f\(1
[ideographic]:　https://example.invalid/g
[bare]: destination
[a label with spaces]: https://example.invalid/h
[標籤]: https://example.invalid/i
[a\]b]: https://example.invalid/j
[a\[b]: https://example.invalid/k
[\ ]: https://example.invalid/l
    A definition closes the paragraph above it, so this line is a code block.

The half that only looks like one. CommonMark reads every line below as an
ordinary paragraph, so every line below is still translated, and so is the
indented line under it, which is that paragraph's lazy continuation and not code.

[x]: /url not a title
    A bare word cannot be a title.

[Ana]: Hello there, she said.
    A line of dialogue is not a link definition.

[spaced]: /two words
    A bare destination may not contain a space.

[unclosed]: <a destination that never closes
    The angle form has no fallback to the bare one.

[junk]: /url "a title" and then some junk
    No further character may occur after the title.

[nested]: /url (a (nested) title)
    A parenthesized title admits no unescaped parenthesis.

[unbalanced]: /url(1
    Parentheses in a bare destination have to balance.

[empty]:
    An empty destination is no destination at all.

[ ]: /url
    A label holding no non-whitespace character is not a label.

[　]: /url
    An ideographic space is whitespace to markdown-it-py's own normalizer.

[a[b]: /url
    A label may not hold an unescaped opening bracket.

[[a]: /url
    Nor one at its start, where the bracket refuses the label outright.

[a\]: /url
    A backslash consumes the bracket, so this label never closes at all.

[]: /url
    There is nothing between the brackets.

[a]b: /url
    The colon does not follow the closing bracket.

[[a]]: /url
    The label opens with a bracket it may not hold.

And the other half of the rule, which is about where the line sits rather than
what is on it: a definition may not interrupt a paragraph, so the line below is
the quote's lazy continuation and renders as its own literal text.

> A blockquote whose paragraph continues past the marker.
[lazy]: /url

Prose after all of it.
