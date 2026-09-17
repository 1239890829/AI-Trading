#!/bin/bash
# Project-local reversible removal; checks and recovery records live in Python.
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/safe_trash.py" "$@"
