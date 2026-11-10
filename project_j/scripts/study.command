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
here=$(dirname -- "$0")
# Diagnostics (standard error, metadata only) are also kept in a local file; standard output,
# with the page link and its token, stays on the screen. The copy ignores Ctrl+C and the window
# closing, so it keeps the lines the service writes while it stops and seals.
logs="$(CDPATH='' cd -- "$here/.." && pwd)/private/logs"
mkdir -p -m 700 "$logs"
log="$logs/study-me-$(date -u +%Y%m%dT%H%M%SZ).log"
echo "诊断日志另存：$log"
{ "$here/start.sh" --study --run-id me --open 2>&1 1>&3 3>&- |
  (trap '' HUP INT TERM PIPE; umask 077; exec tee -a "$log" >&2); } 3>&1
