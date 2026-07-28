A paragraph containing a vertical tab  and a form feed  inline.

A paragraph containing a file separator  and a group separator .

A paragraph containing a record separator  and a next line .

A paragraph containing U+2028   and U+2029   and a nbsp   too.

These are here because str.splitlines() splits on every one of them and
str.split('\n') does not. Swapping to splitlines is therefore not a
behaviour-neutral refactor, and this fixture is what proves it.
