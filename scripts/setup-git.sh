#!/usr/bin/env bash
# Initialize the repository with the AstraKismet-Isida identity and remote.
# Identity is set --local so the rest of the machine keeps its own.
set -euo pipefail

NAME="${LX_GIT_NAME:-AstraKismet-Isida}"
EMAIL="${LX_GIT_EMAIL:-}"
ORG="${LX_GIT_ORG:-AstraKismet}"
REPO="${LX_GIT_REPO:-scriptorium}"

[ -f pyproject.toml ] || { echo "run from the project root" >&2; exit 1; }

if [ -z "$EMAIL" ]; then
  echo "GitHub can keep your address private — see https://github.com/settings/emails"
  read -rp "Commit email: " EMAIL
fi

[ -d .git ] || { git init -b main >/dev/null; echo "initialized repository on branch main"; }

git config --local user.name "$NAME"
git config --local user.email "$EMAIL"
echo "local identity: $NAME <$EMAIL>"

URL="https://github.com/$ORG/$REPO.git"
if git remote | grep -qx origin; then git remote set-url origin "$URL"; else git remote add origin "$URL"; fi
echo "origin: $URL"

cat <<EOF

Next:
  git add -A
  git commit -m 'Initial commit: deterministic localization pipeline'
  git push -u origin main
EOF
