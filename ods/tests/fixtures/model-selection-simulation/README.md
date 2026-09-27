# Model selection before and after the curated-fit selector

`scripts/simulate-model-selection.py` runs `scripts/select-model.py` from an
ODS tree over the 65 hardware envelopes in
`tests/fixtures/model-selection-envelopes.json`, exactly as the Linux and
macOS installers call it. Each pick is then re-checked with the memory
estimator and catalog layouts of the tree the harness runs from, so an older
selector's picks get a real fit column (`corrected GiB`, `fits`, `fits @64K`).

- `before-origin-main-7a328347.md`: the selector and catalog at origin/main
  7a328347 (after #6712), estimated with this change's estimator.
- `after.md`: this change. `tests/fixtures/model-selection-golden.json` pins
  the same picks, and `tests/test-model-selection-matrix.py` checks them.
  For envelopes without a size ceiling the golden file also pins the
  `--profile gemma4` pick (`gemma4`), which
  `tests/test-windows-catalog-selector.ps1` compares with the Windows
  selector.

Regenerate (Linux, from `ods/`):

```bash
git archive origin/main ods/scripts ods/config ods/extensions/services/dashboard-api \
    ods/installers/lib/tier-map.sh | tar -x -C /tmp/before
python3 scripts/simulate-model-selection.py --root /tmp/before/ods \
    --title 'Model selection BEFORE (origin/main <sha>)' --out tests/fixtures/model-selection-simulation/before-origin-main-<sha>.md
python3 scripts/simulate-model-selection.py --title 'Model selection AFTER (this PR)' \
    --out tests/fixtures/model-selection-simulation/after.md
python3 scripts/simulate-model-selection.py --check-golden tests/fixtures/model-selection-golden.json
```
