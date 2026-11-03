# Portal connected-app acceptance

Validation snapshot: 3 November 2026. The latest ordinary Windows/WSL baseline
is Source 4.3.44. Documents, photos and named-feed checks ran after that update;
the earlier component campaigns are labeled below. Physical model smokes used
local Qwen 3.5 9B at 64K context with an 8,192-token output budget. External-app
fixtures used invented data; Google checks used the existing read-only account
connection. Separate copied-runtime budget checks covered 8K and 32K profiles.

| Area | Observed result |
| --- | --- |
| Ordinary update | Installer and an independent postflight completed; existing native configuration and private connection state were preserved. |
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
A Calendar request attempted an unrelated cron operation and was refused; the
final contract change still needs its corresponding live model-date check. The
photo response correctly returned its evidence but also claimed it had clicked
the link without a corresponding action. Those failures remain part of the
qualification; successful clarifications do not replace the original results.

A concurrent-tab run also exposed shared active-conversation selection: a stale
send was refused before starting a task. Recovery selected the original saved
conversation; the document and news native sessions remained separate. The
dashboard correction requires its own completed acceptance before being reported
as passed.

Coffee's native macOS MCP, notes, ntfy and app-provider service is implemented;
its physical Apple Silicon qualification remains a separate handoff. Google,
Calendar and fixed reviewed Connected-app routes still depend on the Linux/WSL
Source Broker. Protected Mac Source staging exists, but its live execution,
automatic triggers and connection activation remain an implementation gap,
not only a physical test gate. See the
[Mac qualification scope](pixel/native-integrations-macos-qualification.md).
Production Google sign-in also depends on the project's official OAuth/domain
verification; local account tests do not establish complete public onboarding.
