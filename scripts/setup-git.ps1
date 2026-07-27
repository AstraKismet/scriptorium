<#
.SYNOPSIS
  Initialize the repository with the AstraKismet-Isida identity and remote.

.DESCRIPTION
  Identity is set with --local, not --global: this project's commits carry the
  AstraKismet-Isida name without changing the identity used by everything else
  on the machine.

  Run from the project root:  .\scripts\setup-git.ps1
#>
[CmdletBinding()]
param(
  [string]$Name  = "AstraKismet-Isida",
  [string]$Email = "",
  [string]$Repo  = "scriptorium",
  [string]$Org   = "AstraKismet",
  [switch]$Ssh
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path "pyproject.toml")) {
  throw "Run this from the project root (pyproject.toml not found here)."
}

if (-not $Email) {
  Write-Host "GitHub can keep your address private. Find the noreply address at"
  Write-Host "  https://github.com/settings/emails  (looks like 12345+user@users.noreply.github.com)"
  $Email = Read-Host "Commit email"
}

if (-not (Test-Path ".git")) {
  git init -b main | Out-Null
  Write-Host "initialized repository on branch main"
} else {
  Write-Host "repository already initialized"
}

git config --local user.name  $Name
git config --local user.email $Email
Write-Host "local identity: $Name <$Email>"

$url = if ($Ssh) { "git@github.com:$Org/$Repo.git" } else { "https://github.com/$Org/$Repo.git" }
if (git remote | Select-String -Quiet '^origin$') {
  git remote set-url origin $url
} else {
  git remote add origin $url
}
Write-Host "origin: $url"

Write-Host ""
Write-Host "Next:"
Write-Host "  git add -A"
Write-Host "  git commit -m 'Initial commit: deterministic localization pipeline'"
Write-Host "  # create the empty repo at https://github.com/organizations/$Org/repositories/new"
Write-Host "  git push -u origin main"
