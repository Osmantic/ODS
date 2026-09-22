# AudioMass for ODS

Waveform and multitrack audio editing served locally. Upstream production commit: `21f5ee1362a47be6f0dbe6e4969a15e43d21b044`, https://github.com/pkalogiros/AudioMass. This is an editor, not another transcription or speech-generation server.

## Install and launch

Install `audiomass` from the ODS library and open its launch link, normally http://localhost:11079/. Change `AUDIOMASS_PORT` if necessary. No API key, inference model, npm installation or server audio device is needed. The image uses the exact source archive with a checksum and a pinned Nginx base. It serves upstream's local scripts, codec workers and WebAssembly files at their expected paths.

## Project workflow

Open project audio through the browser file picker, select and trim the relevant regions, adjust volume/fades or use the multitrack mode to arrange clips. Retain an editable session through the editor's session-save feature and export the resulting audio into the intended `Playground/<project>` using the normal file workflow. Ask Portal to use those actual exported assets in the target project. A mixed-down audio file does not retain independent editable tracks.

Working buffers use the browser's memory; local saved state is scoped to the browser origin. Export files before clearing storage, changing host/port/browser/profile or closing an unsaved session. No Docker document volume or direct Portal workspace mount is created. Audio decoding/encoding support and practical track duration depend on the browser and available client RAM. HTTP health is not proof that a chosen codec can encode successfully.

Microphone recording requires explicit browser permission and a secure context (localhost is normally treated as trustworthy). The recipe does not enable recording automatically or mount host microphone devices into Docker. Prefer opening the app directly if an embedded panel does not grant microphone permission.

## Platforms and licensing

The static-server base provides Linux amd64/arm64 for corresponding ODS Docker adapters on Windows, Linux and macOS. Audio processing uses the client browser's Web Audio/WebAssembly support. There is no CUDA dependency or chat-model/context change.

Original AudioMass code is MIT; bundled libraries/codecs retain their own licenses, including BSD and LGPL. License and third-party notices are served under `/licenses/`; the exact upstream source archive is available at `/source/audiomass-21f5ee1.tar.gz`. Preserve dependency notices and applicable source/relinking obligations when redistributing modified codecs; do not label the entire bundle exclusively MIT.

User-selected URL imports and external information links can contact their respective origins. This recipe configures no remote encoder, account or upload service and does not copy the hosted site's branding URL into the ODS launch address.

## Validation status

Image build, browser editing, recording, session recovery and audio export remain pending for user testing. Schema/Compose/staging checks cover packaging only. A useful manual check is to import a short WAV, trim and fade it, export it, and confirm the duration and sound in the target project; then save and reopen a small multitrack session.
