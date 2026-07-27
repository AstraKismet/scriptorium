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

Identity is set with `--local`, so this project commits as `AstraKismet-Isida`
without touching the identity every other repository on the machine uses.

```powershell
.\scripts\setup-git.ps1
```

It prompts for a commit email — use the GitHub noreply address from
<https://github.com/settings/emails> if you would rather not publish a real one.
Add `-Ssh` if you push over SSH rather than HTTPS.

Then create the empty repository at
<https://github.com/organizations/AstraKismet/repositories/new> (name it
`scriptorium`, no README or license — the project already has both) and push:

```powershell
git add -A
git commit -m "Initial commit: deterministic localization pipeline"
git push -u origin main
```

Verify the identity landed on the commit rather than a global default:

```powershell
git log -1 --format='%an <%ae>'
```

## What is and is not committed

`.lx/docs/` and `.lx/reports/` are regenerable and ignored. `.lx/tm.*.jsonl` —
the translation memory — is deliberately **not** ignored: it holds wording a human
has already approved, and losing it means paying for that review twice.

`.gitattributes` normalizes line endings to LF in the repository while keeping
CRLF for `.ps1` and `.bat` in the working tree, so nothing in `src/` shows up as
modified after a fresh clone.

## Line endings

Git for Windows defaults to `core.autocrlf=true`, which fights `.gitattributes`
on some setups. If files appear modified immediately after cloning:

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
