# Portal owner approval terminal

An awaiting Operations card can open a private terminal in Portal application
chrome. It runs only the installed `ods-pixel-approve JOB PLAN_HASH --confirm`
helper. The helper still requires a fresh host sudo password, reads the protected
plan, and asks the owner to type its unpredictable one-time challenge. This is
not a general shell, a model tool, or an approval inferred from chat text.

The existing local-terminal command remains available. This implementation
supports a non-root Linux host agent (including WSL); macOS and Windows-native
host agents refuse this route. No installer or sudo policy changes are required.

## Custody and limits

- The Dashboard route requires a signed Dashboard sign-in cookie, same-origin
  browser metadata, and HTTPS or a loopback origin. The preview iframe is not
  given access. The host route separately requires existing host-agent auth.
- Opening rechecks the exact protected job/hash through the installed Operations
  status helper. Only an awaiting job with approval required can start a PTY.
- One terminal may exist at a time. A random handle and hashed browser-session
  identity bind all subsequent input, output and cancellation. Handles are POST
  body fields, never URLs or model receipts.
- Only input lines are accepted: 1 KiB UTF-8, no controls, monotonically increasing
  sequence numbers. Lost acknowledgements are not replayed. Input is never stored
  in component state, browser storage, transcripts or application logs. PTY echo
  is disabled before any input. The password field is cleared before sending.
- A session lasts at most 180 seconds, with a 30-second disconnected-owner idle
  deadline and 1 MiB total private output. Output is cursor-addressed so losing an
  HTTP reply does not silently remove part of the protected plan. The UI renders
  text, strips terminal control sequences and offers no hyperlinks or terminal
  escape actions.
- Closing the card, changing job/hash or pressing Stop requests cancellation of
  that exact PTY. The child has a controlling terminal and parent-death signal;
  a fixed Python supervisor retains that parent-death signal while the original
  helper runs. The supervisor revokes credentials on host death; the host also
  kills its private process group and independently revokes sudo credentials.
  A new terminal is offered only after stop and credential cleanup are confirmed.
- Stopping the terminal does **not** cancel an already approved Operations job.
  The broker's separate status receipt remains authoritative. No operation is
  marked approved, cancelled or successful from terminal text or exit alone.
- Output and handles are host-process-local. Host restart loses the dialogue;
  API restart can recover transport to the still-running host session using its
  existing browser handle. Neither restart replays input. This is not an
  audit-log or terminal-export feature.

## Isolated qualification

Run Python tests using an unprivileged Linux interpreter; the PTY suite replaces
credential revocation and never invokes host sudo:

```sh
python -m pytest ods/extensions/services/pixel-agent/tests/test_approval_terminal.py ods/extensions/services/dashboard-api/tests/test_pixel_approval_terminal.py
cd ods/extensions/services/dashboard
npx vitest run src/components/PortalApprovalTerminal.test.jsx src/pages/Pixel.test.jsx
npm run build
```

The disposable Docker fixture tests the original approval helper against actual
sudo/PAM and a protected fixture plan. Its broker is a fixed-argument receipt stub;
it does not install or execute a host operation. Copy the fixture Dockerfile,
`real_approval.py`, and host `approval_terminal.py` into a temporary build context,
then extract `ods/bin/ods-pixel-approve` from the Git blob into that context (LF
bytes, not a Windows checkout with CRLF). Build targets `password` and
`passwordless` separately. Run each with no mounts, no external network and:

```sh
docker run --rm --network none --memory 256m --pids-limit 64 \
  --cap-drop ALL --cap-add SETUID --cap-add SETGID --cap-add DAC_OVERRIDE \
  --cap-add AUDIT_WRITE --cap-add CHOWN --cap-add FOWNER \
  --hostname approval-qa --add-host approval-qa:127.0.0.1 IMAGE
```

Those capabilities exist only inside the disposable test image to allow its
actual sudo/PAM path; neither a privileged container nor the host Docker socket
is mounted. Fixture credentials are public test data, not deployment secrets.
Coverage includes incorrect password, incorrect challenge, correct exact-plan
approval, incorrect plan hash, and refusal of passwordless sudo. Physical host
acceptance and real Operations execution through Portal remain separate checks.

For authenticated crash coverage, use the password image with the same limits,
add `--user 0 --cap-add KILL`, and override its command with
`python3 /qa/real_approval.py --crash-proof-root`. This fixture authenticates as
its unprivileged owner, kills the PTY-owning process while awaiting the exact
challenge, verifies no living private process group and no sudo timestamp file,
then confirms that `sudo -n -v` in a new owner PTY fails. Root is used only inside
this disposable fixture to inspect the protected timestamp and deliver SIGKILL;
this is not a claim about arbitrary host PAM policies or sudo plugins.
