#!/bin/sh
# Repository-scoped uv runtime/cache/environment; no global settings changed.
set -eu
TASK_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
export UV_CACHE_DIR="$TASK_ROOT/project_j/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$TASK_ROOT/project_j/.python"
# uv 0.11.11 lists Python up to 3.14.4; the pinned 3.14.8 build and its digest come from here.
export UV_PYTHON_DOWNLOADS_JSON_URL="$TASK_ROOT/project_j/scripts/python-downloads.json"
exec uv "$@"
