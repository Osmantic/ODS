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
files mode 0600 and the folder mode 0700. The gateway never sees a password, a
token or the vault path.

The gateway runs as the same user, so the service uses separate sockets to tell
owner requests from agent requests. A uid check could not do that.

- **Agent socket:** `/run/ods-pixel-integrations/control.sock`. It accepts only
  agent actions: search and read notes, write notes, notify, and list and call MCP
  tools.
- **Owner socket:** `~/.config/ods/pixel-integrations/owner.sock`. It accepts
  only owner actions: read settings, save, test notification, check tools, and
  approve or decline. The gateway's `ProtectHome=tmpfs` hides this folder, so the
  owner socket is out of its reach. The installer refuses to start the service if
  any gateway bind covers it.

Both sockets refuse any process that is not the owner's user.

## Permissions

Each MCP tool and note writing has one of three policies: **Off**, **Ask first** or
**Allowed**.

Before an MCP server can be turned on, the owner presses **Check tools**. Portal
then lists the server's tools with what each one does, based on the MCP
annotations:

- "Only reads";
- "Only adds things" (the server says `destructiveHint: false`);
- "Can change or delete things" (the default for a tool that is not read-only, as in the MCP specification).

Read-only tools start as Allowed. Every other tool starts as Ask first. If the
server's address or command changes, its tools must be checked again.

An "ask first" action creates an approval in Settings, and an ntfy ping if that is
turned on:

- The approval is bound to the exact server, tool and arguments, or the exact
  note, text and mode.
- It can be used once and expires after 24 hours.
- After the owner approves, Portal repeats the same call.

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

- Linux only for now. macOS and native Windows return "not available on this
  system yet".
- Notifications are plain text, up to 4,000 characters.
- MCP results passed to the model are clipped to 8,000 characters.
- Up to 20 MCP servers, and up to 200 tools per server.
- Scheduled tasks can end by calling `pixel_ods_notify` to report their result.
