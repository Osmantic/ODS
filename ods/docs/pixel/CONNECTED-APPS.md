# Connected apps: Google Calendar and Gmail

Portal can read your calendar and your email, so you can ask "what's on my
calendar tomorrow?" or "did Sam reply about the invoice?". You connect them in
**Settings, Connected apps**. Nothing is connected until you do it, and you can
disconnect at any time.

## What Portal can and can't do

- **Calendar:** Portal reads the events in your primary calendar, from a week ago
  to three months ahead. If you turn on **Suggest calendar changes you approve**,
  it can also suggest adding, moving or deleting an event. Nothing changes on your
  calendar until you approve that exact change in Settings, unless you also let
  Portal add or move your own private events by itself.
- **Email:** Portal reads summaries of your Inbox and of what you sent, for the
  last 180 days: sender, subject, date and a short, cleaned-up excerpt. It never
  sees attachments, and it can't send or delete mail. If you turn on **Write
  Gmail drafts you send yourself**, it can also write a draft, which reaches
  Gmail only after you approve it in Settings. You send it from Gmail.
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
   Calendar changes are off unless you turn them on.
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

## Calendar changes you approve

With **Suggest calendar changes you approve** on, you can ask Portal "move my
dentist appointment to Thursday" or "add lunch with Sam on Friday at noon".
Portal writes the change down and tells you it waits for your approval; the chat
also shows a line that links to Connected apps.

Under the Google card, **Calendar changes from Portal** lists each one with
everything it would write: the title, when, where, the guest list and notes,
whether Google emails the guests, and for a change or deletion, the event as your
calendar has it now.

- **Approve** makes exactly that change, once. If Portal rewrites its suggestion
  after you opened the list, the version you saw is still the one that's made.
- **Decline** removes it. Nothing changes on your calendar.
- If the event changed after Portal suggested something, Google refuses the
  change and the list says so. Ask Portal again.
- If Google doesn't confirm a change, ODS never sends it again. For a new event,
  **Check again** asks Google whether it was added. For a move or a deletion,
  look at your calendar, then **Dismiss** it.

To turn calendar changes on later, press **Change access** on the card and
connect again with the box checked.

## Private events Portal adds or moves by itself

**Add or move your own private events without asking** builds on calendar
changes. With it on, Portal makes two kinds of change without asking first:

- adding an event that has no guests, so Google emails no one;
- moving one of your events to a new time, changing nothing else about it.

Anything else still waits for your approval: guests, invitations, a new title,
place or notes, and every deletion. Portal makes at most 20 of these changes an
hour; the rest are not made, and Portal says so.

While Portal has **Full Access**, it makes none of these changes by itself. ODS
stops them the moment you turn Full Access on, and Portal asks for your approval
instead. They start again when you switch Portal back to Sandbox. Your choice
stays on the card throughout.

**Changes Portal made by itself**, under the Google card, lists this week's,
newest first. **Undo** removes an event Portal added, or moves an event back to
where it was. ODS undoes a change only while the event is exactly as Portal left
it: if you or a guest changed it since, or you added guests, the list says so and
leaves it alone. Each change can be undone once. A change made in the last minute
shows Undo after the next calendar sync.

## Gmail drafts you send yourself

With **Write Gmail drafts you send yourself** on, you can ask Portal "reply to
Sam that Thursday works" or "write to Pat about the invoice". Portal writes the
draft and tells you it waits for your approval; the chat also shows a line that
links to Connected apps. Portal can't send email, with this on or off.

Under the Google card, **Gmail drafts from Portal** lists each draft whole: who
it's for, the subject, the message, and for a reply, the email it answers.

- **Create draft** puts exactly that draft in your Gmail Drafts, once. A reply
  joins the conversation it answers. Open Gmail to read it over and send it.
- **Decline** removes it. Nothing is written to Gmail.
- If Gmail doesn't confirm a draft, ODS never creates it again. **Check again**
  asks Gmail whether the draft is there.
- A draft with a line break in its subject, or with text from an email ODS held
  back as unsafe, can't be created. Decline it and ask Portal again.

To turn drafts on later, press **Change access** on the card and connect again
with the box checked. Google then asks you to allow Gmail drafts.

## Disconnect

Press **Disconnect** on the card. ODS stops syncing, removes its access at
Google, and deletes the sign-in, every summary it saved and every calendar change
still waiting. Portal then stops using your calendar and email. Uninstalling ODS
does the same.

## Turn connected apps off

Set `PORTAL_CONNECTED_APPS_ENABLED=false` in ODS's `.env` file to hide Connected
apps from Settings. A connection you already made keeps working until you
disconnect it; turn the setting back on to reach the card again.

## Get help

If something here doesn't work, ask on the ODS Discord:
<https://discord.gg/4ntNp9MAwC>. For how connected apps work inside ODS, see
[Portal (Pixel)](../PIXEL.md) and the [host agent API](../HOST-AGENT-API.md).
