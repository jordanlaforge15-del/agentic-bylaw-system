#!/usr/bin/env bash
set -euo pipefail

exec codex --ask-for-approval never "$@"
