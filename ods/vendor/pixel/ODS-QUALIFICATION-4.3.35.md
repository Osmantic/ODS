# ODS-maintained Pixel 4.3.35 candidate

Functional source checkpoint: `0194de8a3e81fc5e27f20d87c836956a5587c1d6` on the
public ODS branch `feat/pixel-4.3.35-gmail-drafts`. This record does not claim
an upstream Pixel release or completed fleet acceptance.

The Source Broker gains Gmail drafts that the owner approves. A new answers-file
key, `emailDraftsEnabled`, turns on `pixel_gmail_draft_propose`. It is off by
default and needs the email limb. The tool writes a proposal only; there is no
send tool, and the owner sends a draft from Gmail.

`--approve gmail-draft-…` creates exactly the approved, hash-bound snapshot
through Gmail's `users.drafts.create`, once, through the shared action journal.
The draft's Message-ID is fixed from the proposal and its hash before any Gmail
call, so `--reconcile` finds an unconfirmed draft by `rfc822msgid:` instead of
creating another. A definite Gmail refusal settles as rejected and is never
retried.

A draft is refused before Gmail is called when it has any of these:

- a multi-line subject;
- an invalid or repeated recipient, or a Bcc;
- a projection placeholder;
- what looks like a vendor credential.

Replies take `In-Reply-To`, `References` and the thread from the original message.

The gateway and broker environments carry `PIXEL_LIMB_EMAIL_DRAFTS_ENABLED`. The
renderer, the release contract's Google tool surface and the control plane's chat
environment know the new tool and flag. The version advances from 4.3.34 to
4.3.35, because the existing installer rejects different source at the same
version. OpenClaw, Node, plugin dependency versions, base-image digests, policies
and trust anchors are unchanged.

With the key left out, configure renders drafts off, the renderer denies the new
tool like every other inactive source tool, and nothing else changes in the
rendered configuration. ODS keeps drafts off until an owner turns them on in
Connected apps.

Automated evidence, all with fakes and no network or real accounts:

- `tests/test_source_broker.py`:
  - an approved draft is created once and never sent, with exactly the approved
    recipients, subject, body and Message-ID;
  - a reply threads with the message it answers;
  - drafts off and unsafe drafts never reach Gmail;
  - an unconfirmed draft stays unconfirmed until Gmail shows it, is then found
    by its Message-ID, and is never created twice;
  - a Gmail refusal settles without retry.

  The new cases fail against the 4.3.34 broker.
- `tests/source-broker-plugin.test.mjs`: the draft tool registers only while
  drafts and the email limb are on, and writes only a proposal.
- `tests/e2e.sh`: generated environments carry drafts off by default, and a
  render with drafts on allows the tool.
- `tests/test_email_prompt_injection.py` and `tests/test_source_pressure.py` pass
  unchanged.

Before accepting this release:

- run the held source upgrade on real Linux/WSL installations and through a
  macOS native update;
- verify the installed source version;
- confirm that a disconnected ODS install keeps the same onboarding contract,
  `openclaw.json` and bootstrap bytes;
- exercise a real Gmail draft only with a dedicated test account.

The release remains a candidate until that evidence is recorded. No
qualification signature or private upstream source was used.
