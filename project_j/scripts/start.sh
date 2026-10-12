#!/bin/sh
# Single loopback backend; static assets are preloaded before any classroom runs.
set -eu
TASK_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$TASK_ROOT"
exec project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-serve "$@"
