# Portal integrations

Portal can use four of the owner's own services. Each is off until the owner sets it
up in **Settings → Portal integrations**:

- **Notes (Obsidian):** search, read, and add to notes in a vault folder.
- **Notifications (ntfy):** send a notification to the owner's phone or computer.
  This includes pings when an action is waiting for approval.
- **MCP servers:** connect any MCP server over Streamable HTTP or as a local stdio
  command. Portal sees only the tools the owner allowed.
- **Home Assistant:** a preset for Home Assistant's built-in MCP server.

Portal gets three tools: `pixel_ods_notes`, `pixel_ods_notify` and `pixel_ods_mcp`.

## How it works

```
Settings page ──► dashboard-api ──► host agent ──► owner.sock ─┐
                                                               ├─ pixel-integrations.service
Portal gateway (pixel-ods plugin) ──────────► control.sock ────┘   (runs as the owner)
                                                                    ├─ vault folder
                                                                    ├─ ntfy
                                                                    └─ MCP servers
```

`pixel-integrations.service` runs as the owner and does every integration action.
It holds the settings and secrets in `~/.config/ods/pixel-integrations/`, with
files mode 0600 and the folder mode 0700. Normal integration-tool replies do not
include configuration credentials or the vault root. Configured servers receive
their credentials and must be trusted; literal redaction cannot stop a malicious
server from encoding or transmitting a secret.

The gateway runs as the same user, so the service uses separate sockets to tell
owner requests from agent requests. A uid check could not do that.

- **Agent socket:** `/run/ods-pixel-integrations/control.sock`. It accepts only
  agent actions: search and read notes, write notes, notify, and list and call MCP
  tools.
- **Owner socket:** `~/.config/ods/pixel-integrations/owner.sock`. It accepts
  only owner actions: read settings, save, test notification, check tools, test a
  server, restart a local server, approve or decline, and clear the activity list.
  In Sandbox, the gateway's home mount hides this folder from ordinary paths;
  the installer refuses a bind that exposes it, including a canonical alias.
  This is not a different-user security boundary.

Both sockets refuse any process that is not the owner's user.

**Full Access:** the gateway can use the owner's files and commands. It can
therefore reach the owner's integration state and secrets outside the agent
endpoint. Off/Ask first/Allowed are enforced by that endpoint, not an absolute
restriction on separately authorized Full Access commands. Different socket
paths with the same UID do not prevent such access. The installer and Settings
do not certify Sandbox isolation for Full Access.

## Permissions

Each MCP tool and note writing has one of three policies: **Off**, **Ask first** or
**Allowed**.

Before an MCP server can be turned on, the owner presses **Check tools**. Portal
then lists the server's tools with what each one does, based on the MCP
annotations:

- "Only reads";
- "Only adds things" (the server says `destructiveHint: false`);
- "Can change or delete things" (the default for a tool that is not read-only, as in the MCP specification).

These labels are server reports, not independently verified effects. See the
[MCP tool specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).

On the first check, read-only tools start as Allowed and every other tool starts as
Ask first. A tool that appears on a later check starts as **Off** and is marked
*New*, so a server cannot add an allowed tool behind the owner's back. **Test
connection** connects without changing anything and says whether tools were added
or removed since the last check. If the server's address or command changes, its
tools must be checked again.

An "ask first" action creates an approval in Settings, and an ntfy ping if that is
turned on:

- The approval is bound to the exact server, tool and arguments, or the exact
  note, text and mode, and the destination/configuration that was reviewed.
  Replacing a vault, changing a server's endpoint, credential or schema, or
  disabling and re-enabling it invalidates the old approval. Returning to an
  earlier setting does not revive it.
- Saving settings or checking tools revokes pending and approved requests, even
  for an unrelated integration. Repeat the action to request a new approval.
- It can be used once and expires after 24 hours.
- After the owner approves, Portal repeats the same call. A scheduled task does
  not wait: it reports that the action needs approval, and its next run within 24
  hours with the same arguments goes ahead.
- The ping can carry an **Open approvals** button. It only opens the Settings
  page, where the owner still signs in; the link grants nothing. Use an address
  the phone can reach.

## Local servers

A server started as a local command runs as the owner, with the owner's files.
Only the owner can add one. Settings says so next to the command.

- It starts on first use and keeps running between calls, so a slow start (`npx`,
  `uvx`) is paid once and a server that remembers things (a browser, a database)
  works. It stops after 10 minutes without calls, when it is turned off or
  removed, or with **Restart**; it then starts again on its next use.
- It runs in its own process group. Stopping it stops everything it started.
- A request that gets no answer within 30 seconds (90 for the first start) stops
  the server; the next use starts it again. Initialization, queueing, catalog
  pages and execution also share a 120-second session budget. HTTP requests
  have a 20-second absolute deadline, including headers and body. Cleanup
  finishes before the service reports a terminal result.
- The service has a minimal `PATH`. It also looks in `~/.local/bin`, `~/bin`,
  `~/.npm-global/bin`, `~/.cargo/bin`, `/usr/local/bin`, `/snap/bin`, Homebrew and
  the newest nvm Node. Otherwise give the full path, as `which npx` shows it.
- Secret environment variables (such as `GITHUB_TOKEN`) are stored with the other
  secrets and never shown back. The server gets only those, plus `HOME`, `USER`,
  `LANG` and the `PATH` above, not the service's own environment.
- An optional working folder; the default is the owner's home.
- Its error output is kept with configured plaintext secret values masked. The
  owner sees the last lines in Settings when it fails; the model only gets a
  short sentence. This does not prevent an untrusted program from encoding or
  independently transmitting a credential.
- Questions a server asks the client are answered so it never waits: `ping`,
  `roots/list` (no roots), and "not supported" for anything else.
- The service allows up to 2 GB of memory and 512 tasks for itself and its local
  servers. Its sandbox blocks new namespaces, so a server that sandboxes a browser
  itself may not start.

Notes:

- Portal never deletes or overwrites a note. It only appends to a note or creates
  a new one.
- Hidden folders (such as `.obsidian`), symbolic links and paths outside the vault
  are refused.
- Search covers up to 5,000 notes of up to 1 MB each.

## Setting up

**ntfy:**

- Install the ntfy extension from the Extensions page, or use any ntfy server.
- Enter its address, a topic, and a user and password (or an access token).
- Press **Send test notification**.

**Home Assistant:**

1. In Home Assistant, add the *Model Context Protocol Server* integration.
2. Create a long-lived access token on the profile page.
3. In ODS, press **Add Home Assistant**. Enter `http://<home-assistant>:8123/api/mcp`
   and the token.
4. Press **Check tools**, choose permissions, turn the server on and save.

The integration exposes the entities shared with Assist.

**Obsidian:** enter the vault's full path. The service runs as the owner, so it
can read any folder the owner can.

## Limits

- Linux, including Windows through WSL2, which runs the same installer code. Not
  yet tried on a real Windows machine.
- Not on macOS yet: Settings and the tools say "Portal integrations aren't
  available on macOS yet". Porting needs a launchd job for the service and a
  matching rule that keeps its state folder out of the native gateway's sandbox.
- Sign-in with OAuth is not supported; servers take a token.
- The activity list keeps the latest 500 entries and can be cleared.
- Notifications are plain text, up to 4,000 characters.
- MCP results passed to the model are clipped to 8,000 characters.
- A cancelled request is not submitted if cancellation arrives before sending.
  After submission, cancellation keeps native admission until the service
  settles. If the connection loses the response, Portal reports an unknown
  outcome and must not automatically repeat an action that may have happened.
- Up to 20 MCP servers, and up to 200 tools per server.
- Scheduled tasks can end by calling `pixel_ods_notify` to report their result.
