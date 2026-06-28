#!/usr/bin/env bash
# Make this skill available to Claude Code by symlinking it into ~/.claude/skills/.
# The repo stays the source of truth; the symlink means edits here take effect immediately.
set -euo pipefail
cd "$(dirname "$0")"
REPO="$(pwd)"
DEST="$HOME/.claude/skills/nonprofit-close-reconciliation"

mkdir -p "$HOME/.claude/skills"
if [ -L "$DEST" ] || [ -e "$DEST" ]; then
  echo "Replacing existing $DEST"
  rm -rf "$DEST"
fi
ln -s "$REPO" "$DEST"
echo "Linked: $DEST -> $REPO"
echo "Claude Code will now discover the 'nonprofit-close-reconciliation' skill."
