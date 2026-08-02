# Windows setup

Written for `C:\Users\Isida\Documents\workspace\scriptorium`, with Claude Code
running in that directory and the remote under the `AstraKismet` organization.

## Place the project

Unzip the delivered archive so that `pyproject.toml` sits directly inside the
project folder:

```powershell
cd $HOME\Documents\workspace
Expand-Archive -Path $HOME\Downloads\scriptorium.zip -DestinationPath . -Force
cd scriptorium
Get-ChildItem   # pyproject.toml, src\, skill\, tests\ should be here
```

## Python

Python 3.9 or newer. The pipeline itself has no dependencies, so this works
immediately:

```powershell
py -m scriptorium --version
py -m scriptorium providers
```

A virtual environment is worth it once you start editing, mostly so `lx` lands on
PATH and the dev tools stay out of the system interpreter:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
lx --version
pytest -q
```

If `Activate.ps1` is blocked, allow local scripts for your user once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## Git identity and remote

**This is already done.** The repository was initialized on `main`, the identity
was set with `--local` so nothing else on the machine is affected, and the first
commit records the delivered state unmodified. Confirm rather than repeat:

```powershell
git log -1 --format='%an <%ae>'    # AstraKismet-Isida <305370422+astrakismet-isida@users.noreply.github.com>
git config --local core.autocrlf   # input
```

If you ever need to reproduce the setup on another machine, it is four commands —
there is deliberately no script, because a script that stops halfway is worse
than a list you can read:

```powershell
git init -b main
git config --local user.name  "AstraKismet-Isida"
git config --local user.email "305370422+astrakismet-isida@users.noreply.github.com"
git config --local core.autocrlf input
```

### The SSH identity trap on this machine

The remote **must** use the `github-astrakismet` alias, not `github.com`:

```powershell
git remote add origin github-astrakismet:AstraKismet/scriptorium.git
```

`~/.ssh/config` maps that alias to `~/.ssh/id_ed25519_astrakismet`. The *default*
key resolves to a different GitHub account that is not a member of the
`AstraKismet` organization, so a `git@github.com:AstraKismet/…` remote
authenticates as the wrong user and the push is rejected. Verify before pushing:

```powershell
ssh -T github-astrakismet    # Hi astrakismet-isida!
ssh -T git@github.com        # a different account — this is why the alias exists
```

The same alias form is used by `AstraKismet/worldthread-core`, so the two
repositories behave identically.

### Pushing workflow files

The `gh` token on this machine holds `repo` but **not** `workflow` scope. Over
SSH that does not matter. If you switch the remote to HTTPS, `.github/workflows/`
changes will be refused until you run:

```powershell
gh auth refresh -h github.com -s workflow
```

## What is and is not committed

`.lx/state.db` and `.lx/reports/` are regenerable and ignored — the database
carries `-wal` and `-shm` sidecars while a command is running, and they are
ignored with it. `.lx/tm.*.jsonl` —
the translation memory — is deliberately **not** ignored: it holds wording a human
has already approved, and losing it means paying for that review twice.

`.gitattributes` normalizes line endings to LF in the repository while keeping
CRLF for `.ps1` and `.bat` in the working tree, so nothing in `src/` shows up as
modified after a fresh clone.

## Line endings

Git for Windows defaults to `core.autocrlf=true`, which fights `.gitattributes`
on some setups. **This repository already sets `core.autocrlf=input` locally**, so
the problem should not appear here. On a fresh clone elsewhere, if files show as
modified immediately:

```powershell
git config --local core.autocrlf input
git rm --cached -r . ; git reset --hard
```

## Local models

A local backend keeps drafting free and offline. With Ollama installed:

```powershell
ollama pull qwen2.5:14b-instruct
ollama serve
```

The shipped `local` provider already points at `http://localhost:11434/v1`.
Confirm the wiring before translating anything real:

```powershell
lx providers
lx run examples\sample.md --lang zh-TW --dry-run
```

CPU inference is slow. If requests time out, raise `providers.local.timeout` and
lower `batch.size` in `lx.config.json` — smaller batches finish sooner and a
failure costs less.

## Claude Code

Open the folder and start:

```powershell
cd $HOME\Documents\workspace\scriptorium
claude
```

`CLAUDE.md` at the root carries the architectural invariants and the commands, so
the session starts with the constraints already loaded. Point new work at it
rather than re-explaining the design each time.

## The workbench

```powershell
lx web
```

Serves on `http://localhost:8787` and opens a browser. It binds to loopback only;
Windows Firewall should not prompt. Stop it with Ctrl-C.
