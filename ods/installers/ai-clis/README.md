# Optional AI command-line tools

Set `ODS_INSTALL_AI_CLIS=true` before running the installer to opt in to Claude
Code 2.1.280 and Codex CLI 0.156.1. The default install does not install them or
bootstrap Node.js for them. Explicitly selected OpenCode is independent of this
setting. These optional tools require Node.js 22+ and npm.

The installer copies the committed manifest and lockfile into a new private
directory under `~/.ods/ai-clis`, then runs `npm ci --ignore-scripts`. The lock
contains exact versions and SHA-512 integrity for all platform packages, including
transitive/optional dependencies. An absent platform binary or an integrity error
stops publication of new launchers. Claude's native binary is launched directly
from its verified platform package; no dependency lifecycle script runs.

Successful installs publish `claude` and `codex` launchers in
`~/.ods/ai-clis/bin` and retain `install-receipt.json` beside the installed
lockfile. The receipt records the lock SHA-256, direct versions, and every
package's integrity. The installer adds that bin directory to the user's PATH.
Existing global npm packages and earlier ODS tool directories are preserved.

To update, review upstream package metadata and native-binary layouts, change
the exact versions in `package.json`, then regenerate without executing scripts:

```sh
npm install --package-lock-only --ignore-scripts --registry=https://registry.npmjs.org
node --test install.test.mjs
```

Do not replace this flow with `npm install -g`: npm global installation does not
use this lockfile. Real macOS/Windows tool execution and Rosetta compatibility
still require qualification on those platforms. Use native Node.js on Apple
Silicon so the matching ARM64 packages are installed.
