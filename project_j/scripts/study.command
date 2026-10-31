#!/bin/sh
# One-click study as "me": double-click, or drag this file to the right side of the Dock.
# The page opens once the speech models have loaded; stop with Ctrl+C or by closing the window.
pids=$(lsof -nP -tiTCP:4242 -sTCP:LISTEN 2>/dev/null | tr '\n' ',')
if [ -n "$pids" ]; then
  holder=$(ps -o command= -p "${pids%,}")
  case $holder in
    *project-j-serve*) echo "Project J 已在运行：页面链接在原来的终端窗口里；要重新开始，先在那个窗口按 Ctrl+C 停止。" ;;
    *) printf '4242端口被其他程序占用，Project J 未启动；先关闭它：\n%s\n' "$holder" ;;
  esac
  exit 1
fi
exec "$(dirname -- "$0")/start.sh" --study --run-id me --open
