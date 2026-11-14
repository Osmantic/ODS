# Portal integrations

Portal can use the owner's own services. Each is off until the owner sets it up in
**Settings → Portal integrations**:

- **Notes (Obsidian):** search, read, and add to notes in a vault folder.
- **Notifications (ntfy):** send a notification to the owner's phone or computer.
  This includes pings when an action is waiting for approval.
- **MCP servers:** connect any MCP server over Streamable HTTP or as a local stdio
  command. Portal sees only the tools the owner allowed.
- **Home Assistant:** a preset for Home Assistant's built-in MCP server.
- **Documents (Paperless-ngx):** search documents and read their text.
- **Photos (Immich):** find photos by place and date, and by what is in them when
  Immich's machine learning runs.
- **Watched pages (changedetection.io):** watch a page, or a product's price, and
  get a notification when it changes.
- **News (FreshRSS):** the unread stories, and a daily digest sent to the phone.
- **Scheduled tasks:** a notification when one finishes or fails.

Portal gets three tools: `pixel_ods_notes`, `pixel_ods_notify` and `pixel_ods_mcp`. The
apps add no tools of their own: each app the owner turns on is listed in `pixel_ods_mcp`
as a built-in server (`documents`, `photos`, `watch`, `news`) next to the owner's MCP
servers. The pinned gateway accepts at most 32 tools from the Portal plugin, so new
integrations should not add tools either.

## How it works

```
Settings page ──► dashboard-api ──► host agent ──► owner.sock ─┐
                                                               ├─ pixel-integrations.service
Portal gateway (pixel-ods plugin) ──────────► control.sock ────┘   (runs as the owner)
                                                                    ├─ vault folder
                                                                    ├─ ntfy
                                                                    ├─ MCP servers
                                                                    └─ apps (Paperless-ngx, Immich,
                                                                       changedetection.io, FreshRSS)
```

`pixel-integrations.service` runs as the owner and does every integration action.
It holds the settings and secrets in `~/.config/ods/pixel-integrations/`, with
files mode 0600 and the folder mode 0700. The gateway never sees a password, a
token or the vault path.

The gateway runs as the same user, so the service uses separate sockets to tell
owner requests from agent requests. A uid check could not do that.

- **Agent socket:** `/run/ods-pixel-integrations/control.sock`. It accepts only
  agent actions: search and read notes, write notes, notify, list and call MCP
  tools, use the apps, and report a finished scheduled task.
- **Owner socket:** `~/.config/ods/pixel-integrations/owner.sock`. It accepts
  only owner actions: read settings, save, test notification, check tools, test a
  server, test an app, restart a local server, approve or decline, and clear the
  activity list. The gateway's `ProtectHome=tmpfs` hides this folder, so the
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

On the first check, read-only tools start as Allowed and every other tool starts as
Ask first. A tool that appears on a later check starts as **Off** and is marked
*New*, so a server cannot add an allowed tool behind the owner's back. **Test
connection** connects without changing anything and says whether tools were added
or removed since the last check. If the server's address or command changes, its
tools must be checked again.

An "ask first" action creates an approval in Settings, and an ntfy ping if that is
turned on:

- The approval is bound to the exact server, tool and arguments, or the exact
  note, text and mode.
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
  the server; the next use starts it again. The service itself never waits longer.
- The service has a minimal `PATH`. It also looks in `~/.local/bin`, `~/bin`,
  `~/.npm-global/bin`, `~/.cargo/bin`, `/usr/local/bin`, `/snap/bin`, Homebrew and
  the newest nvm Node. Otherwise give the full path, as `which npx` shows it.
- Secret environment variables (such as `GITHUB_TOKEN`) are stored with the other
  secrets and never shown back. The server gets only those, plus `HOME`, `USER`,
  `LANG` and the `PATH` above, not the service's own environment.
- An optional working folder; the default is the owner's home.
- Its error output is kept. The owner sees the last lines in Settings when it
  fails; the model only gets a short sentence.
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

## Apps

Each app runs on this computer, usually from the ODS extension catalog. Their server ids
are reserved, so an MCP server cannot be named after them. Settings
fills in the address the catalog uses; change it for an app installed elsewhere.
The owner gives each app a key, which stays with the other secrets, and **Test**
says what answered. An app can be tested before it is turned on, but it can be
turned on only with its key.

- **Documents:** Portal searches Paperless-ngx's full text and reads a document's
  text (up to 12,000 characters). It cannot change or delete documents. When more
  documents match than it shows, it says how many.
- **Photos:** Portal searches by place (a city or country Immich knows), by date,
  or by what is in a photo. Searching by what is in a photo needs Immich's machine
  learning container, which the catalog does not include; Portal says so instead
  of guessing, and **Test** says which searches work and what to turn on. Results
  are links that open the photo in Immich, with a note when more photos match.
- **Watched pages:** Portal lists, adds and removes watches in changedetection.io.
  Adding and removing are **Ask first** or **Allowed**. The service looks at the
  watches every 2 minutes and notifies the owner when a page changed. A price
  watch reads the product's price from the page and notifies once when it is at
  or under the limit, and again only after it has gone back above it. A watch is
  first only recorded, so adding one does not notify. Its tools say which need approval,
  as an MCP tool set to Ask first does. changedetection.io refuses pages on private
  networks (such as a router or another local service) unless its
  `ALLOW_IANA_RESTRICTED_ADDRESSES` setting is turned on. Settings lists the
  watched pages, with each price limit and the last price Portal saw. A price is
  read only for a watch that changed since the last look.
- **News:** Portal reads the unread stories from FreshRSS (up to 40), grouped by
  feed. FreshRSS needs API access allowed and an API password.

## Scheduled tasks and the daily digest

Scheduled tasks are OpenClaw's own cron jobs, however they were made. When one of
Portal's tasks finishes, the plugin passes the result to the service, which sends
a notification as the owner chose: when it finishes or fails, only when it fails,
or never. A failure notification is sent with high priority. OpenClaw retries a
failed task a few times within minutes, so only the first failure in a row
notifies; the next good run sends "works again", also when the owner chose only
failures.

The daily news digest is one more scheduled task, named *Daily news digest*. The
plugin keeps it in step with the owner's setting about once a minute: it adds the
task, changes its time, or removes it. At the chosen time Portal reads the unread
stories that no earlier digest sent and writes a short brief: one line per story,
grouped by topic, at most 15 stories, without links. Each story goes out in one
digest only, whether or not the owner read it; the service remembers the last 400
stories it sent, and a story is remembered only once its digest was sent. It is sent through ntfy, cut to fit one message (ntfy
sends a longer one as a file). The digest run may use only `pixel_ods_mcp`, so it
cannot wander off searching the web. If Portal did not read the news in that run,
or the run fails or takes more than 10 minutes, the owner gets the plain brief of
new story titles instead, so the digest always arrives. **Send a digest now**
in Settings sends that plain brief right away, and its stories count as sent.
OpenClaw retries a failed digest run, so a run that finds nothing new within two
hours of a digest sends nothing, and "Today's digest could not be made" (when
FreshRSS cannot be reached) is sent once until a digest goes out again.
Asked for the news in a chat, Portal reads all unread stories, sent or not.

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

**Apps:**

- *Paperless-ngx:* the API auth token from your profile (top right).
- *Immich:* an API key from Account settings → API keys.
- *changedetection.io:* the API key from Settings → API.
- *FreshRSS:* your user name, and an API password from Settings → Profile. Allow
  API access under Settings → Authentication.

## Limits

- Linux, including Windows through WSL2, which runs the same installer code. Not
  yet tried on a real Windows machine.
- Not on macOS yet: Settings and the tools say "Portal integrations aren't
  available on macOS yet". Porting needs a launchd job for the service and a
  matching rule that keeps its state folder out of the native gateway's sandbox.
- Sign-in with OAuth is not supported; servers take a token.
- The activity list keeps the latest 500 entries and can be cleared.
- Notifications are plain text, up to 4,000 characters; the digest stays under
  3,900 bytes.
- The service forgets which scheduled tasks were failing when it restarts, so a
  task still failing then notifies once more. It also forgets when the last digest
  went out (it keeps which stories it sent), so a retried digest run right after
  a restart may say "No new stories" once.
- MCP results passed to the model are clipped to 8,000 characters.
- Up to 20 MCP servers, and up to 200 tools per server.
- Scheduled tasks can also end by calling `pixel_ods_notify` with their own message.
- Up to 10 results per document or photo search, and up to 100 watched pages.
