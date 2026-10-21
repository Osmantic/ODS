#!/usr/bin/env bash
# ============================================================================
# ODS — Service base URLs
# ============================================================================
# Purpose:  Print a service's base URL from config/service-urls.json, the one
#           service map that bash and dashboard-api both read.
# Expects:  SCRIPT_DIR (the ODS root) or ODS_SERVICE_URLS_FILE; python3 or
#           ODS_PYTHON_CMD.
# Provides: ods_service_url <service-id>
# Modder notes: each entry is the service's manifest default_host and port
#           (tests/contracts/test-service-url-map.py enforces it). An unknown
#           id fails; there is no hardcoded fallback.
# ============================================================================

ods_service_url() {
    local service_id="$1"
    local map_file="${ODS_SERVICE_URLS_FILE:-${SCRIPT_DIR:-.}/config/service-urls.json}"
    "${ODS_PYTHON_CMD:-python3}" -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    urls = json.load(handle)
if sys.argv[2] not in urls:
    sys.exit("unknown service id in %s: %s" % (sys.argv[1], sys.argv[2]))
print(urls[sys.argv[2]])
' "$map_file" "$service_id"
}
