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

To defer work from chat, type the instructions and choose **Schedule** beside Send.
Give the task a name, choose when it should run, then select **Schedule task**.
Opening the form does not send the draft or edit a note. Cancel keeps the draft
in chat. Scheduled tasks use the permissions in effect when they run. In ordinary
chat, **Send** can perform an **Allowed** note edit immediately.

Portal gets three tools: `pixel_ods_notes`, `pixel_ods_notify` and `pixel_ods_mcp`. The
apps add no tools of their own: each app the owner turns on is listed in `pixel_ods_mcp`
as a built-in server (`app:documents`, `app:photos`, `app:watch`, `app:news`) next to the owner's MCP
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
files mode 0600 and the folder mode 0700. Normal integration-tool replies do not
include configuration credentials or the vault root. Configured servers receive
their credentials and must be trusted; literal redaction cannot stop a malicious
server from encoding or transmitting a secret.

The gateway runs as the same user, so the service uses separate sockets to tell
owner requests from agent requests. A uid check could not do that.

- **Agent socket:** `/run/ods-pixel-integrations/control.sock`. It accepts only
  agent actions: search and read notes, write notes, notify, list and call MCP
  tools, use the apps, and report a finished scheduled task.
- **Owner socket:** `~/.config/ods/pixel-integrations/owner.sock`. It accepts
  only owner actions: read settings, save, test notification, check tools, test a
  server, test an app, restart a local server, review the exact pending action,
  approve or decline, and clear the activity list.
  In Sandbox, the gateway's home mount hides this folder from ordinary paths;
  the installer refuses a bind that exposes it, including a canonical alias.
  This is not a different-user security boundary.

Both sockets refuse any process that is not the owner's user.

The service publishes `discovery.json` beside the agent socket: a bounded 0600
snapshot of checked, enabled custom MCP connections and their non-Off tool names
and Ask-first flags. It contains no remote descriptions, argument schemas,
endpoints, commands, private paths or credentials. Startup, successful Owner
save/check and the existing watcher renew it atomically; it expires after five
minutes. It is configuration metadata, never live provider health or authority.
Portal adds a quoted, at most 2048-byte view to the current model prompt without
calling a provider or storing it in chat history. Small contexts of 8K or less,
managed team roles, probes and automatic review continuations keep their existing
contract. Missing, expired, corrupt or unsafe files omit the hint; MCP `list`
remains the complete metadata API and every action still follows host policy.

Private URLs do not enable direct agent networking. Public web tools, shell and
unapproved browser access retain their private-destination restrictions. The
configured-app/MCP tool uses the integration service instead: its catalog lists
metadata, and each operation still requires an enabled, checked server/tool and
the owner's Off/Ask first/Allowed policy. A server ID never selects an arbitrary
endpoint. The configured provider owns its network boundary; for example, the
owner's changedetection instance can monitor a LAN page it can reach. Trust that
provider before enabling it. Page contents and tool output cannot grant access,
approve actions or widen the owner's request.

The native macOS deployment uses `com.ods.pixel-native-integrations` and the
persistent agent socket `/private/var/lib/ods-pixel-integrations/control.sock`.
The owner socket remains in the real account's private state folder, outside
the gateway's separate HOME. Every native owner request must prove knowledge
of a root-provisioned owner-only credential against a fresh per-connection
challenge. The exact request and protocol domain are authenticated, with no
bare-socket fallback or automatic retry. The credential, state and logs are
explicitly excluded by the new native gateway profiles in both modes.

These native mechanisms still require real Apple Silicon launchd, ACL and
Seatbelt qualification; offline tests do not certify alias or broader owner
process isolation. See the [native qualification gate](../../../docs/pixel/native-integrations-macos-qualification.md).

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

When a Notes task needs owner approval, Pixel can call the Notes tool with
`action: request_approval` and the exact path, text and append/create mode.
With **Ask first**, this creates or finds one Settings request. Proposing an
already approved edit leaves its grant unused. Only a later `write` with the
same destination and content can spend the grant. **Allowed** still supports
a one-step write. **Off** refuses both operations.

For an explicit request to prepare an edit without doing anything else,
`action: prepare` remains separate: it never writes, spends a grant or sends a
notification, including under **Allowed**. Both proposal paths keep the same
exact Owner preview and require a later matching `write` to save the note.

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
- **Review request** loads the exact proposed note text, or server, tool and
  arguments, before Settings offers **Approve**. Full payloads stay in private
  owner state and are omitted from agent replies and approval lists. Older
  requests without a saved payload, damaged snapshots and previews containing
  configured secrets cannot be approved in this screen. Decline proposals you
  cannot verify; an identical declined preparation is not automatically reopened.
  The owner API's existing exact-id decision remains available to other owner clients.
- Approving does not execute the action or resume a finished chat. Ask Portal to
  try the same action again. A scheduled task does not wait: it reports that the
  action needs approval, and its next run within 24 hours with the same arguments
  goes ahead.
- The ping can carry an **Open approvals** button. It only opens the Settings
  page, where the owner still signs in; the link grants nothing. Use an address
  the phone can reach.

The note writer preserves the supplied text body as UTF-8, including trailing
Markdown spaces, blank lines and CRLF line endings. It adds a final LF only if
the body does not already end with LF. Appending leaves all existing file bytes
unchanged and keeps the existing block-separator rules: no separator for an empty
note or a note ending in two LF characters (`\n\n`), one LF after another
LF-terminated note, otherwise two LF characters. These separators are outside the
exact body shown in the approval preview; creating a note has no prefix separator.

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

## Apps

Each app runs on this computer, usually from the ODS extension catalog. Their server IDs
are `app:documents`, `app:photos`, `app:watch` and `app:news`. Existing custom MCP
servers keep their IDs, including names such as `news` or `photos`. Settings
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
  feed. The `unread` tool accepts an optional exact `feedTitle` to read only one
  subscribed feed, selecting its provider stream before the story limit. Missing
  or duplicate titles stop the read rather than including other feeds. Omitting
  the field keeps the all-feeds read. This does not send a notification or change
  the daily digest. FreshRSS needs API access allowed and an API password.

## Scheduled tasks and the daily digest

Scheduled tasks are OpenClaw's own cron jobs, however they were made. When one of
Portal's tasks finishes, the plugin passes the result to the service, which sends
a notification as the owner chose: when it finishes or fails, only when it fails,
or never. A failure notification is sent with high priority. OpenClaw retries a
failed task a few times within minutes, so only the first failure in a row
notifies; the next good run sends "works again", also when the owner chose only
failures.

Scheduled-task notices start **Never**, including when an older ntfy connection
is upgraded. Existing explicit **All** or **Failures only** choices are retained.
Turning on chat or approval notifications does not silently opt into task summaries.

The daily news digest is one more scheduled task, named *Daily news digest*. The
plugin keeps it in step with the owner's setting about once a minute: it adds the
task, changes its time, or removes it. A private creation journal and the native
returned job ID identify this task; jobs with the same name or description are
not automatically adopted or removed. Ambiguous recovery stops without changing
those jobs. At the chosen time Portal reads the unread
stories that no earlier digest sent and writes a short brief: one line per story,
grouped by topic, at most 15 stories, without links. A delivery batch contains
only the stories whose factual title lines fit the notification; other stories
remain eligible for a later digest. The optional model brief is bound to the
news read in that exact native run. A chat or another task's read does not
establish that provenance. The notification stays within one ntfy message (ntfy
sends a longer one as a file). The digest run may use only `pixel_ods_mcp`, so it
cannot wander off searching the web. If Portal did not read the news in that run,
or the run fails or takes more than 10 minutes, the owner gets the plain brief of
new story titles instead. **Send a digest now** in Settings sends that plain
brief right away through the same delivery admission.
OpenClaw retries a failed digest run, so empty scheduled digests stay quiet after
the first attempt on that news schedule's local day, including after restart.
An explicit **Send a digest now** may still report that no new stories exist.
"Today's digest could not be made" (when
FreshRSS cannot be reached) is sent once until a digest goes out again.
Delivery records are private and persist across restarts. An admitted send whose
result is lost is reported as uncertain in activity and is not automatically
repeated: a network failure cannot prove whether ntfy accepted a message. This
avoids silent replay, not a guarantee of delivery. A full history stops new
admission without discarding records. Asked for the news in a chat, Portal
reads unread stories independently of digest delivery history.

An uncertain or refused delivery appears in the News card with its exact message
and the current notification destination. The owner may explicitly retry that
same message after acknowledging that it might already have arrived. Changed
settings require a fresh review; a used retry cannot be submitted twice. This
owner action never fetches or silently adds other stories to the saved message.

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

- Linux, including Windows through WSL2, which runs the same installer code.
  Feature-specific installed qualification is recorded in the
  [composition acceptance record](../../../docs/PORTAL-COFFEE-INTEGRATION.md).
- Native macOS deployment, exact legacy/new upgrade and recovery contracts,
  owner-channel authentication and retirement are implemented. Physical Apple
  Silicon qualification remains required; an older deployment without the
  service reports unavailable until updated through the native installer.
- Sign-in with OAuth is not supported; servers take a token.
- The activity list keeps the latest 500 entries and can be cleared.
- Notifications are plain text, up to 4,000 characters; the digest stays under
  3,900 bytes.
- Scheduled-task failure streaks and observed run times persist, so repeated,
  older or replayed terminal events do not send the same notice after restart.
  Delivery can still fail; the activity list records the notification error.
- MCP results passed to the model are clipped to 8,000 characters.
- A cancelled request is not submitted if cancellation arrives before sending.
  After submission, cancellation keeps native admission until the service
  settles. If the connection loses the response, Portal reports an unknown
  outcome and must not automatically repeat an action that may have happened.
- Up to 20 MCP servers, and up to 200 tools per server.
- Scheduled tasks can also end by calling `pixel_ods_notify` with their own message.
- Up to 10 results per document or photo search, and up to 100 watched pages.
