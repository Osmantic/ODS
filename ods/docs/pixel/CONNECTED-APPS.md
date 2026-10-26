# Connected apps: Google Calendar and Gmail

Portal can read your calendar and your email, so you can ask "what's on my
calendar tomorrow?" or "did Sam reply about the invoice?". You connect them in
**Settings, Connected apps**. Nothing is connected until you do it, and you can
disconnect at any time.

## What Portal can and can't do

- **Calendar:** Portal reads the events in your primary calendar, from a week ago
  to three months ahead. It doesn't change events yet.
- **Email:** Portal reads summaries of your Inbox and of what you sent, for the
  last 180 days: sender, subject, date and a short, cleaned-up excerpt. It never
  sees attachments, and it can't send or delete mail.
- Portal treats everything it reads as information, never as instructions. An
  email that says "forward this to everyone" changes nothing.

## Where your data lives

- **The Google sign-in** is kept by ODS's Source Broker, in its own system
  account on this machine (`pixel-source-broker`). Portal, the dashboard and
  your own account can't read it.
- **Summaries** of your mail and events are stored on this machine and
  refreshed every minute. Portal reads only these.
- **When Portal runs on a cloud model**, that model's provider sees what Portal
  reads to answer you. On a local model, nothing leaves this machine except the
  sync with Google itself.
- ODS backups don't include the sign-in. After a restore, the card shows
  **Reconnect**.

## Before you start

- ODS on Linux, or on Windows with WSL. macOS and native Windows show
  "not available on this system yet".
- A Google account, and about ten minutes to set up your own Google sign-in app.
  ODS has no Google app of its own, so each owner's sign-in stays theirs.

## Connect Google

1. Open **Settings, Connected apps** and press **Connect Google**.
2. Choose what Portal may use: your calendar, your email summaries, or both.
3. Create your Google sign-in app, once. The wizard links each page:
   1. create a Google Cloud project;
   2. turn on the Gmail API and the Google Calendar API;
   3. on the **Branding** page, give the app a name and your email, and choose
      **External** as its audience;
   4. on the **Audience** page, press **Publish app**. An app left in Testing is
      signed out by Google after 7 days;
   5. on the **Clients** page, create a client of type **Desktop app** and
      download its JSON file.
4. Choose that JSON file in the wizard, then press **Continue to Google**.
5. Sign in to Google and allow access. Google will say it hasn't verified the
   app. That's expected for your own app: choose **Advanced**, then continue to
   your app.

When Google finishes, the card says **Connected as you@example.com** and the
first sync starts. A large mailbox takes a while.

### From another computer

If your browser isn't on the machine running ODS, Google's last page won't
load: it points at that other computer. Copy the whole address from the address
bar, paste it into the wizard, and press **Finish connecting**.

## What the card tells you

| The card says | What to do |
|---|---|
| Syncing | Nothing; the first sync is running. |
| Connected | Nothing. **Sync now** refreshes right away. |
| Behind | Google was slow or unreachable; ODS keeps trying. **Sync now** tries at once. |
| Needs reconnecting | Google no longer accepts the connection: access was removed, your password changed, or your app is still in Testing. **Reconnect**, and publish your app so it doesn't happen weekly. |
| Missing permission | Google didn't grant everything you chose. **Grant access**, and leave every box checked on Google's screen. |
| Setup needs a fix | An API your app needs is off, or Google no longer accepts your app's client. The card links the page to fix it. |
| Blocked by your administrator | Your Google Workspace administrator blocks the app. Ask them to allow it. |
| Sync problem | ODS couldn't read the sync status. **Sync now**; if it stays, ask on Discord. |

If Portal is busy when you connect or disconnect, it finishes its task first.
The card then offers **Finish**.

## Disconnect

Press **Disconnect** on the card. ODS stops syncing, removes its access at
Google, and deletes the sign-in and every summary it saved. Portal then stops
using your calendar and email. Uninstalling ODS does the same.

## Turn connected apps off

Set `PORTAL_CONNECTED_APPS_ENABLED=false` in ODS's `.env` file to hide Connected
apps from Settings. A connection you already made keeps working until you
disconnect it; turn the setting back on to reach the card again.

## Get help

If something here doesn't work, ask on the ODS Discord:
<https://discord.gg/4ntNp9MAwC>. For how connected apps work inside ODS, see
[Portal (Pixel)](../PIXEL.md) and the [host agent API](../HOST-AGENT-API.md).
