# Private dashboard state on macOS

The dashboard API stores image attachments and retained chat responses in two
Docker named volumes. Their private SQLite files stay on the Docker VM's local
filesystem. Other dashboard data and model caches keep their existing locations.

The installer prepares these volumes before starting the API. On an existing
installation, the first migration requires the API to be stopped:

```sh
./ods-macos.sh stop
./ods-macos.sh start
```

Migration retains the original `data/pixel-images` and `data/pixel-chat-results`
directories and private snapshots under `data/.dashboard-private-stores`.
Successful later starts reuse the volumes; they never copy those old directories
over newer state. Existing image deletion fences and incomplete chat attempts
are retained. Restarting the API does not make an incomplete attempt complete.

Use the ordinary ODS backup/restore commands. Image snapshots still appear as
`data/pixel-images` in archives. Short-lived chat delivery receipts remain
excluded. The existing restriction on backing up/restoring managed native Pixel
state still applies; this storage change does not make native archives supported.

An interrupted restore blocks API starts until its saved transaction is resolved.
`data/.dashboard-private-stores/image-restore-active.json` identifies the private
transaction directory. With the API stopped, the transfer helper can roll it back:

```sh
python3 installers/macos/lib/dashboard-private-state.py rollback \
  --install-dir "$PWD" --path /exact/transaction/directory/from/the/record
```

Keep the transaction, original directories and volumes if rollback reports an
error. Do not delete a receipt, change ownership or import an old host directory
to bypass a refusal. A changed legacy database after an interrupted migration
requires reconciliation of both retained copies before another migration.
`ods-uninstall.sh --keep-data` preserves the volumes; ordinary removal accounts
for their exact installation and creation receipts, including failed first starts.
