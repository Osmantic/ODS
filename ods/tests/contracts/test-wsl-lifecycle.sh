#!/usr/bin/env bash
# Pure ownership/order checks on all platforms; real native Windows checks only
# when run on Windows. Never starts/stops a WSL distro or system service.
set -euo pipefail
cd "$(dirname "$0")/../.."
python3 tests/contracts/test-wsl-stack.py -v
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
            -File tests/contracts/test-wsl-lifecycle.ps1
        ;;
    *) printf '%s\n' 'SKIP: native Windows lifecycle tests require Windows; no Scheduler/WSL runtime claim.' ;;
esac
