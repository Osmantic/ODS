# Mobile remote portal

ODS runs inference on the server. Android Termux and iOS a-Shell act as
client-only entry points to the existing ODS Talk portal.

```bash
bash install.sh --server https://ods.example.test
```

The mobile dispatcher normalizes that origin to
`https://ods.example.test/talk`, stores it in
`~/.config/ods/mobile-portal-url`, and installs `~/.local/bin/ods-mobile` on
Termux or `~/bin/ods-mobile` on a-Shell. The launcher uses
`termux-open-url` on Termux or `open` on a-Shell. If neither command is
available, it prints the portal URL for manual use.

Use an existing `/talk` URL when the server is already exposed at that path.
Embedded credentials, query parameters, and fragments are rejected so bearer
tokens, passwords, and magic links are not persisted in launcher
configuration.

For automation, use `--no-open`. Use `--dry-run` to inspect the normalized URL
and paths without writing state. `ODS_MOBILE_CONFIG_DIR` and
`ODS_MOBILE_BIN_DIR` override the default destinations.

This bootstrap does not install Docker, inference runtimes, or server services
on the phone. The target ODS server must already be reachable from the device.
