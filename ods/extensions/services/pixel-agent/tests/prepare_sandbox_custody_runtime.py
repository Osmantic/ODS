"""Create a disposable exact-source SDK fixture; never patch installed bytes."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
source = Path(sys.argv[1]).resolve(strict=True)
target = Path(sys.argv[2]).resolve()
assert not target.exists(), 'Fixture destination must be new'
assert source != target and not source.is_relative_to(target)
assert json.loads((source / 'package.json').read_text())['version'] == '2026.6.33'
target.mkdir(mode=0o700)
runtime = target / 'runtime'
shutil.copytree(source, runtime)
# npm may hoist dependencies beside the package. This read-only resolution
# link is confined to the disposable fixture; all repaired files are copies.
dependencies = source.parent if source.parent.name == 'node_modules' else source.parent / 'node_modules'
if dependencies.is_dir():
    (target / 'node_modules').symlink_to(dependencies.resolve(), target_is_directory=True)
spec = importlib.util.spec_from_file_location('custody_repair', root / 'host/openclaw_tool_recovery.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)
proofs = []
for layer, name in repair.SANDBOX_CUSTODY_MODULES.items():
    before = (source / 'dist' / name).read_bytes()
    proofs.append(repair.repair(runtime, target / ('state-' + layer), module_name=name,
        manifest_path=root / f'host/openclaw-sandbox-custody-{layer}.json'))
    assert (source / 'dist' / name).read_bytes() == before
(target / 'proof.json').write_text(json.dumps({'runtime': str(runtime), 'installedUnchanged': True, 'repairs': proofs}, indent=2))
print(runtime)
