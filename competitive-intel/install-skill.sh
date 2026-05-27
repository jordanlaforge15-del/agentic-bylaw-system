#!/usr/bin/env bash
# Installs the competitive-monitor skill into .claude/skills/ so Claude Code
# can discover it. Run from the repo root:
#
#   bash competitive-intel/install-skill.sh
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_SRC="${REPO_ROOT}/competitive-intel/SKILL.md"
SKILL_DST="${REPO_ROOT}/.claude/skills/competitive-monitor/SKILL.md"

if [ ! -f "$SKILL_SRC" ]; then
  echo "ERROR: ${SKILL_SRC} not found." >&2
  exit 1
fi

mkdir -p "$(dirname "$SKILL_DST")"
cp "$SKILL_SRC" "$SKILL_DST"
echo "Installed competitive-monitor skill → ${SKILL_DST}"
