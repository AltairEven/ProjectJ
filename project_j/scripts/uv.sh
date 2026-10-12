#!/bin/sh
# Repository-scoped uv runtime/cache/environment; no global settings changed.
set -eu
TASK_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
export UV_CACHE_DIR="$TASK_ROOT/project_j/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$TASK_ROOT/project_j/.python"
exec uv "$@"
