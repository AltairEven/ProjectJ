#!/bin/sh
# One-click study as "me": double-click, or drag this file to the right side of the Dock.
# The page opens once the speech models have loaded; stop with Ctrl+C or by closing the window.
if nc -z 127.0.0.1 8765 2>/dev/null; then
  echo "Project J 已在运行：页面链接在原来的终端窗口里；要重新开始，先在那个窗口按 Ctrl+C 停止。"
  exit 1
fi
exec "$(dirname -- "$0")/start.sh" --study --run-id me --open
