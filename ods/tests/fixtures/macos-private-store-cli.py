"""Install the real launch dispatcher with storage preparation isolated.

The shell fixtures test CLI pull/recreate/error propagation with a mock Docker
daemon. Actual migration, dependency closure, custody and backup operations are
covered by test_macos_dashboard_private_state.py. Keep the launch argument
mapping in production code rather than copying it into these shell fixtures.
"""
from pathlib import Path
import shutil
import sys

source = Path(__file__).resolve().parents[2]
root = Path(sys.argv[1])
recipe = root / "installers/macos/dashboard-private-state.compose.yaml"
recipe.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(source / "installers/macos/dashboard-private-state.compose.yaml", recipe)
helper = recipe.parent / "lib/dashboard-private-state.py"
helper.parent.mkdir(exist_ok=True)
original = source / "installers/macos/lib/dashboard-private-state.py"
helper.write_text('''import importlib.util, os
spec=importlib.util.spec_from_file_location("real_private_state", %r)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def isolated(self, root, flags, **options):
    self.root=root
    self.env=dict(os.environ)
    self.binary="docker"
    self.compose_prefix=[]
    self.flags=module.flags_for(root, flags)
    self.requires_stores=False
module.Stores.__init__=isolated
module.main()
''' % str(original))
