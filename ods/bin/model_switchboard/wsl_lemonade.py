"""One-release compatibility alias for the renamed WSL runtime bridge.

Scripts shipped before round F import ``model_switchboard.wsl_lemonade``.
That name now resolves to the same module object as ``wsl_runtime``, so a
patch or call through either name reaches one implementation. Delete this
alias in the release after round F.
"""

import sys

from . import wsl_runtime as _wsl_runtime

sys.modules[__name__] = _wsl_runtime
