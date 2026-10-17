#!/usr/bin/env bash
# Look around a machine before deploying (spec 16.3 step 1). Safe to run anywhere; prints, never changes anything.
set -u
section() { printf '\n==== %s ====\n' "$1"; }
section "uname";        uname -a; echo "machine: $(uname -m)"
section "os-release";   cat /etc/os-release 2>/dev/null | head -5 || echo "n/a"
section "lscpu";        (lscpu 2>/dev/null | head -20) || echo "n/a"
section "memory";       (free -m 2>/dev/null) || echo "n/a"
section "disk";         (df -h . 2>/dev/null) || echo "n/a"
section "python";       (python3 --version 2>&1) || echo "no python3"
section "docker";       (docker --version 2>&1 && docker info --format '{{.ServerVersion}} {{.OSType}}/{{.Architecture}}' 2>&1) || echo "no docker"
section "ports";        (ss -ltn 2>/dev/null | head -20) || echo "n/a"
section "outbound";     for u in https://pypi.org https://github.com; do
                          if command -v curl >/dev/null; then
                            code=$(curl -s -o /dev/null -I -m 5 -w '%{http_code}' "$u" || echo fail); echo "$u -> $code"
                          else echo "curl missing"; fi
                        done
section "structured check"
if command -v python3 >/dev/null; then python3 "$(dirname "$0")/check_platform.py" "$@"; fi
