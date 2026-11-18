# ============================================================================
# ODS Windows-native — process supervisor package
# ============================================================================
# Part of: installers/windows/native/supervisor/
# Purpose: Run the native ODS services (llama-server, dashboard-api) on
#          Windows without Docker or WSL, and keep them running.
#
# Expects: Python 3.12 (the bundled embeddable runtime), stdlib only.
# Provides: plan    — pure config validation and launch plan
#           runner  — Windows imperative shell (Job Object, logs, restarts)
#           __main__ — `python -m supervisor --config <supervisor.json>`
#
# Modder notes:
#   Keep plan.py free of I/O. Everything that touches processes, files or the
#   network lives in runner.py and takes injectable collaborators so it can be
#   unit-tested on Linux.
# ============================================================================
