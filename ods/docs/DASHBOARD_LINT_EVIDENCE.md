# Dashboard lint remediation (PB-012)

On 2026-09-23, the original configuration reported 775 warnings: 773 unused
bindings and two unused suppression directives. ESLint parsed JSX but lacked
React's JSX usage rule, so imports and components used in JSX were incorrectly
reported as unused.

Added the exact development dependency `eslint-plugin-react` 7.37.5 and enabled
only `react/jsx-uses-vars`, following the [upstream configuration documentation](https://github.com/jsx-eslint/eslint-plugin-react#configuration-new-eslintconfigjs).
This left 19 unused bindings and 32 now-obsolete suppression directives.
Removed the obsolete directives, unused imports, unused bindings and one unused
helper. Kept calls with side effects and the existing state setters; the
Markdown span renderer still strips its AST `node` before forwarding DOM props.

`npm run lint` now enforces `--max-warnings 0` and reports no errors or warnings.
No warning budget, path exclusion or new suppression directive was introduced.
The final local run passed 197 test files / 1,596 tests and the production build.
The production dependency audit against the official npm registry found zero
advisories. Runtime React behavior and existing test coverage were preserved.

These are local working-tree results. Native three-platform CI at the frozen
release candidate remains required. Existing test-environment diagnostics, such
as jsdom media stubs, are distinct from the lint gate and were not suppressed.
