"""Build-time only: refuse any source drift before applying the reviewed patch."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

root = Path('/opt/hermes')
delivery = Path(__file__).parent
manifest = json.loads((delivery / 'source-hashes.json').read_text())
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
for name, hashes in manifest.items():
    if digest(root / name) != hashes['before']:
        raise RuntimeError('Unsupported Hermes source: ' + name)
subprocess.run(['git', 'apply', '--check', str(delivery / 'manual-resume.patch')], cwd=root, check=True)
subprocess.run(['git', 'apply', str(delivery / 'manual-resume.patch')], cwd=root, check=True)
for name, hashes in manifest.items():
    if digest(root / name) != hashes['after']:
        raise RuntimeError('Patched Hermes source mismatch: ' + name)
# Use the exact upstream generator, and retain both generated contracts.
spec = importlib.util.spec_from_file_location('ods_contract_generator', root / 'scripts/gen_gateway_contracts.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
generator.TS_OUT.write_text(generator.render_ts())
generator.OPENRPC_OUT.write_text(generator.render_openrpc())
receipt = {'resumeContract': 'manual-v1', 'sourceFiles': manifest,
           'patchSha256': digest(delivery / 'manual-resume.patch'),
           'licenseSha256': digest(root / 'LICENSE'),
           'generated': {str(p.relative_to(root)): digest(p) for p in (generator.TS_OUT, generator.OPENRPC_OUT)}}
(delivery / 'build-manifest.json').write_text(json.dumps(receipt, indent=2) + '\n')
