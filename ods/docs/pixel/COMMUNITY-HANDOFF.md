# Portal public-beta community testing

Use a deliberate public-beta revision and record it before testing. This guide
replaces a historical operational handoff; it does not describe live machines,
running jobs, staged images, current PR state, or completed release acceptance.

## What to test

Start with [the qualification matrix](PUBLIC-BETA-QUALIFICATION.md). Prioritize
installation and lifecycle, actual model identity, research accuracy, browser
interaction with generated applications, completion after interruption, file
and image delivery, and installed provider/access journeys. Test one capability
at a time, including a reproducible failure or recovery case where practical.

For search, use [the native search guide](../../extensions/services/pixel-agent/NATIVE-SEARCH.md).
Existing owner-selected settings may differ from new-install defaults; record
the observed provider rather than assuming an upgrade changed it.

## Report an actionable result

Include:

- exact ODS source SHA and relevant image/model versions;
- OS and hardware class, serving model/backend, and effective context/output limits;
- reproducible user steps, expected result, and observed result;
- whether evidence comes from a fixture, build, installed runtime, or real UI interaction;
- redacted logs or synthetic artifacts and explicit unverified cases.

Do not publish credentials, personal files, workstation paths, internal host
names, session identifiers, or raw support bundles. Consult
[support bundle guidance](../SUPPORT-BUNDLE.md) before sharing diagnostics.
A source checkout, open PR, loaded image, or successful publication is not a
claim that the intended runtime or user journey has passed. Follow
[Release validation](../RELEASE_VALIDATION.md) for release acceptance.
