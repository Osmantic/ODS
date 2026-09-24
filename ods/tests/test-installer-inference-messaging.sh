#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/installers/lib/ui.sh"
EXTERNAL_LLM_URL=''
LEMONADE_EXTERNAL=false
ODS_MODE=lemonade
AMD_INFERENCE_MANAGED=true
for placement in wsl-windows-lemonade linux-container; do
    AMD_INFERENCE_RUNTIME_MODE="$placement"
    [[ "$(ods_ui_inference_scope)" == local ]]
    ods_select_lore_messages
    [[ "${LORE_MESSAGES[0]}" == "${ODS_LOCAL_LORE_MESSAGES[0]}" ]]
done
echo 'PASS: managed Windows and Linux inference uses local messaging'
LEMONADE_EXTERNAL=true
[[ "$(ods_ui_inference_scope)" == external ]]
LEMONADE_EXTERNAL=false
EXTERNAL_LLM_URL='http://example.test/v1'
[[ "$(ods_ui_inference_scope)" == external ]]
EXTERNAL_LLM_URL=''
AMD_INFERENCE_MANAGED=false
[[ "$(ods_ui_inference_scope)" == external ]]
echo 'PASS: explicit external endpoints and unmanaged Lemonade retain external messaging'
ODS_MODE=cloud
[[ "$(ods_ui_inference_scope)" == cloud ]]
ODS_MODE=local
[[ "$(ods_ui_inference_scope)" == local ]]
echo 'PASS: cloud and ordinary local modes remain distinct'
