# Portal connected-app acceptance

Validation snapshot: 3 November 2026. The contributor's normal Windows/WSL
installation of `ef0cc5672` now passes the Calendar journey below with its
Source 4.3.45 pin `3fe50f1d75d13295bfbe964bfcd327a91df15814`.
The composed Source 4.3.46 candidate still needs applicable installed acceptance;
the earlier result does not certify new source or SDK bytes. Documents, photos
and named-feed checks used the earlier Source 4.3.44 baseline; component
campaigns are labeled below. Physical model smokes used
local Qwen 3.5 9B at 64K context with an 8,192-token output budget. External-app
fixtures used invented data; Google checks used the existing read-only account
connection. Separate copied-runtime budget checks covered 8K and 32K profiles.

That Windows installation ran `992e5716c`. Its independent
postflight preserved the complete native configuration, Google grant, model
profile, integration state, existing tasks and histories. The later `bb3b8feeb`
namespace descriptions were adopted as an explicitly scoped two-file component
update, with another independent preservation check. This is not a second full
ordinary-install qualification.

The later ordinary update to `ef0cc5672` completed, followed by an independent
Root postflight. The complete native configuration, original Google grant and
integration secrets, model/sampler profile, existing tasks and histories, prior
backups and retained local extension files were preserved. Its services and
sockets were healthy, runtime access was verified, and no model/source hold
remained. The model stayed local Qwen 3.5 9B; no weights were replaced.

One fresh request through the actual installed Portal frontend asked for only
the number of events on an explicit owner-local day. The model independently
selected `pixel_calendar_count` with a typed civil-day interval. The resulting
native tool-call ID, exact chat/request identity, terminal host ledger and
transport/UI response all agree. The local Calendar projection was fresh,
complete and unfiltered, with the correct São Paulo midnight boundaries despite
the calendar's UTC timezone. The answer matched its complete count. No event
records, unrelated tools or mutation calls were used. The model returned a
short correct sentence instead of the requested bare number; that formatting
miss remains separate from functional success. This single journey does not
establish general model reliability, nonempty live-account coverage or OAuth
refresh behavior.

Windows startup initially failed before service code ran because repeated
Docker Desktop projections had accumulated thousands of duplicate WSL mounts.
An owned private-namespace kernel experiment reproduced the amplification.
Scoped recovery stopped only Portal's projection consumers and removed verified
empty owned mounts with ordinary unmounts, preserving volumes and model data.
Services then started normally and the ordinary update above completed. A
private projection alone loses later mount-replacement propagation; neither
container `rslave` nor a separate shared peer group prevented amplification.
This recovery is not evidence that a general prevention change has shipped.

A second Windows 11 laptop upgraded its everyday WSL2 installation to the
combined candidate `44d02e933` through the ordinary Windows installer. The
owner reconnected the saved remote model and verified the same route. Portal
showed the Google card and Notes controls. One dated task ran with the browser
closed. Its requested answer appeared once in history, and removing the task
restored the empty active list. All four model files retained their hashes.

Google was disconnected on this laptop. Two fresh Portal chats asked about
tomorrow's availability. Both completed requests explained that Calendar was
unavailable and made no claim that the day was empty.

On the installed `44d02e933` Calendar code, a synthetic Los Angeles all-day
event appeared in the preceding local evening and disappeared from the evening
it occupied. The count tool placed it correctly. The list now resolves dates
using the calendar's timezone and exclusive end. The registered-tool regression
covers both sides of those boundaries in Los Angeles and Kathmandu. An older
projection with uncertain all-day timing flags possible matches and cannot
certify an empty slot they might occupy.

| Area | Observed result |
| --- | --- |
| Ordinary update | Installer and an independent postflight completed; existing native configuration and private connection state were preserved. |
| Calendar — installed `ef0cc5672` | One real local 9B request selected the native count tool, used the requested civil-day bounds and returned a count supported by fresh complete data. No event records or mutations were requested. The response used a sentence rather than a bare number. |
| Concurrent conversations | Two independent Chrome conversations retained their own histories after reload and accepted subsequent messages; native ledgers/sessions remained distinct, with no tools or cross-conversation text. One model reply shortened its own test code; the literal-answer oracle remains failed separately from storage isolation. |
| Documents | Portal searched the connected library, read the invoice instead of its packing-slip decoy, and returned the correct amount and due date. |
| Photos | Portal applied the requested city and exact day, excluded adjacent-day/other-city decoys, and returned the actual photo link. |
| Named news feed | Portal selected the exact feed through `feedTitle`; reading sent no notification. A later explicit send produced one notification containing only that feed's two stories and their returned links. |
| Native daily digest | With no active ODS UI, the native clock triggered the managed job. Its actual model read and summary were bound to the native run and durable delivery record; exactly one notification included the two fresh stories, with no generic completion notice. No manual cron run was used. |
| Local MCP process — earlier components | Cold discovery and actual directory reads worked. Repeated reads reused the child; it closed after the real idle timeout. Later reads and Owner Restart reconnected without restarting global services. |
| Home Assistant — earlier components | After clarification, native preparation created exact approval requests. Approval alone caused no device change; explicit retries consumed each approval once and switched only the QA entity on and off. |
| Scheduled notifications — earlier components | Native callbacks exercised All, Failures and Never, repeated-failure suppression and recovery notices. This establishes notification behavior, not arithmetic accuracy. |
| No-tool request — earlier components | A normal Portal arithmetic smoke returned the requested answer without selecting tools. |
| Cold tool schemas | A copied native runtime preserved the complete registered Calendar and Gmail schemas through persistence into its next loopback provider request, with the initial tool surface, core contract and output budget unchanged. |

These observations do not establish perfect small-model behavior. Initial Home
Assistant requests asked for approval in chat without creating a request. Some
scheduled arithmetic was incorrect despite correct notification-policy behavior.
A Calendar request attempted an unrelated cron operation and was refused. The
photo response correctly returned its evidence but also claimed it had clicked
the link without a corresponding action. Those failures remain part of the
qualification; successful clarifications do not replace the original results.

The original concurrent-tab failure remains recorded: a stale send was refused
before starting a task, and recovery selected the original saved conversation.
The installed correction now passes the distinct-conversation workflow above;
same-conversation editing still uses an optimistic stale-writer check.

The earlier Calendar selection failures remain recorded. An earlier native read established a
fresh, complete count for the correct owner-local day. Later ordinary requests
instead selected an invented MCP server or public web search and answered with
an unsupported empty count, with no native Calendar read. The MCP/native API
descriptions and refusal guidance now distinguish their namespaces and preserve
Off policies, but those wording changes have not closed the live-model failure.
The next investigation must verify actual native discovery availability and
selection; another ungrounded empty answer is not successful acceptance. The
installed `ef0cc5672` journey above closes that specific local request with
actual native evidence. The composed Source 4.3.46 and its updated native
admission gate must be qualified separately before this PR is ready.

Coffee's native macOS MCP, notes, ntfy and app-provider service is implemented;
its physical Apple Silicon qualification remains a separate handoff. Google,
Calendar and fixed reviewed Connected-app routes still depend on the Linux/WSL
Source Broker. Protected Mac Source staging exists, but its live execution,
automatic triggers and connection activation remain an implementation gap,
not only a physical test gate. See the
[Mac qualification scope](pixel/native-integrations-macos-qualification.md).
Production Google sign-in also depends on the project's official OAuth/domain
verification; local account tests do not establish complete public onboarding.
