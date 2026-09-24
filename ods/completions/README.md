# Shell completion

## Fish 3.6 and newer

From the `ods/` directory, install both autoload files:

```fish
mkdir -p ~/.config/fish/completions
cp completions/ods.fish completions/ods-cli.fish ~/.config/fish/completions/
```

Open a new Fish session, then try `ods gpu <TAB>`, `ods logs <TAB>`, `ods preset load <TAB>` or `ods update --<TAB>`. Both `ods` and `ods-cli` are supported. Main commands, nested subcommands, common built-in services and selected flags are suggested only at the matching argument position. Custom service IDs can still be typed manually.

Saved preset suggestions read directories with `meta.txt` and `extensions.list` under `INSTALL_DIR/presets`, then `ODS_HOME/presets`, then `ODS_INSTALL_DIR/presets`, or `~/ods/presets` when no override is set. For a custom installation resolved through a script hint or symlink, explicitly set `INSTALL_DIR` in Fish. No `.env` or preset values are sourced, and completion never starts ODS, Docker or a network call.

Validation: `python3 tests/test-fish-completions.py` with Fish installed. Remove the two copied files to uninstall completion. Existing Bash completion remains available in `ods-cli.bash`.

The implementation uses Fish's native [completion API](https://fishshell.com/docs/3.6/cmds/complete.html), not Bash compatibility mode.
