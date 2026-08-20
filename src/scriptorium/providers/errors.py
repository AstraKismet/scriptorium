"""The provider failure type, alone in a module that imports nothing.

`cli.main` catches `ProviderError` so that a backend failure is one sentence and
exit 2 rather than a traceback, and an `except` clause needs the name at module
scope. Importing it from `base` would pull `urllib.request` — and with it `ssl`,
`http.client`, `socket` and fifteen `email` submodules — into every `lx` command,
including `lx --help` on a bare interpreter. Measured 2026-08-20: that import
roughly doubles the cost of importing `scriptorium.cli`, 41 ms to 77 ms.

So the type lives here, `base` re-exports it, and nothing about where it is
defined is visible to anyone catching it.
"""


class ProviderError(RuntimeError):
    pass
