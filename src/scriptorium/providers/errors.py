"""The provider failure type, in a module of its own.

`cli.main` catches `ProviderError` so that a backend failure is one sentence and
exit 2 rather than a traceback. Its `except` tuple is evaluated only once
something has been raised, so the name has to be bound by then on every path,
and a module-scope import is the one placement that cannot get that wrong.

This module was created on 2026-08-20 so that binding the name would not load
the provider transport, and it never did that. Python executes
`providers/__init__.py` before it binds a submodule, that file imports `base` to
build `KINDS`, and until 2026-09-11 `base` imported `urllib.request` at module
scope — so this import put `ssl`, `http.client`, `socket` and the `email`
package into every `lx` command, `lx --help` included. Found on 2026-09-06;
`docs/decisions.md` of that date.

Since 2026-09-11 what keeps the transport off the import path is `base` itself,
which imports it inside `Provider._request`, the one function that uses it.
Measured that day on 3.12, median of seven warm runs: `import scriptorium.cli`
went from 69 ms to 42 ms, and on 3.9 through 3.12 `lx --help` loads none of
those modules. `tests/test_startup_imports.py` holds both. This module plays no
part in it — importing `base`, or the package, is exactly as cheap.

It stays because moving the class would change `ProviderError.__module__`,
which every traceback naming the class prints, and every import spelling this
path would move with it; that buys nothing. `base` and the package re-export
the class, so where it is defined is invisible to anyone catching it.
"""


class ProviderError(RuntimeError):
    pass
