# ODS Windows Desktop (preview)

ODS Windows Desktop runs the real ODS dashboard and a local AI model directly
on Windows: **no Docker, no WSL, no administrator rights**. It installs for
your Windows account only, starts by itself when you sign in, and listens on
`127.0.0.1` only, so nothing leaves the PC. The design and its review steps
are tracked in [RFC #7968](https://github.com/Osmantic/ODS/issues/7968).

This is an opt-in preview. `.\install.ps1` and the
[Docker Desktop + WSL2 path](WINDOWS-QUICKSTART.md) are unchanged.

## What you get today

- **Local chat** in the ODS window: answers stream as they are written,
  follow-up questions keep their context, chats are saved in the window and
  can be saved as Markdown or JSON.
- **The model the ODS catalog picks for your PC's memory**, run by the pinned
  llama.cpp build on the CPU.
- **A Start menu and desktop icon** that opens ODS in its own Microsoft Edge
  app window (or your default browser when Edge is missing). Right after you
  sign in it shows "ODS is starting" for a few seconds, then opens by itself.
- **An Apps & Features entry** to remove it, which asks before deleting your data.

Not in the preview yet: extensions, switching models from the dashboard
(rerun setup instead), GPU acceleration, voice, agents and remote access.

## Requirements

| | |
|---|---|
| Windows | Windows 10 version 1803 or later, or Windows 11, on an Intel or AMD (x64) processor |
| Memory | 8 GB minimum; 16 GB recommended |
| Disk | About 6 GB free on the drive that holds your user profile |
| Account | A normal (non-Administrator) PowerShell window |
| Internet | For the first setup only (llama.cpp, Python, the model) |

The model engine needs the Microsoft Visual C++ runtime (2015-2022, version
14.40 or newer). Most PCs already have it. If yours does not, setup offers to
install it from microsoft.com; Windows asks for permission once, and setup
runs the installer only after Windows confirms it is signed by Microsoft.

## Install (testers)

Until the built dashboard ships as a release file, build it once with Node.js
from an ODS checkout that contains the Desktop branches:

```powershell
cd ods\extensions\services\dashboard
npm ci
npm run build
cd ..\..\..\..
powershell -NoProfile -ExecutionPolicy Bypass -File ods\installers\windows-desktop.ps1
```

Setup runs six steps and says what each one does: checks, the Visual C++
runtime, the model engine, the AI model, starting the model, and the ODS app.
The model is the large download; if it is interrupted, run the same command
again and it continues where it stopped. At the end ODS opens in its window.

Run the same command again at any time to repair or update. It keeps your
model, port and chats. Add `-Repair` to re-check every file hash, `-Port <n>`
to choose the window's port, and `-NonInteractive` for a scripted run (it
never installs prerequisites; it explains what is missing instead).

## Where things live

| Path | What |
|---|---|
| `%LOCALAPPDATA%\ODS\desktop` | The ODS app, its Python, launcher, logs and data |
| `%LOCALAPPDATA%\ODS\lemonade\models` | The AI model (the folder name is shared with the WSL Portal path) |
| `%LOCALAPPDATA%\ODS\lemonade\portal-runtime` | The model server's plan, private key and log |
| `%LOCALAPPDATA%\ODS\llama.cpp` | The pinned llama.cpp build |

Two sign-in tasks run them: `ODSLlamaServerRuntime-<your SID>` (the model)
and `ODSDesktopApi-<your SID>` (the app). Both run as you, with limited rights.

## Remove

Use **Settings > Apps > Installed apps > ODS Desktop (preview)**, or run
`%LOCALAPPDATA%\ODS\desktop\uninstall.ps1` with `-KeepData` or `-RemoveData`.
Uninstall stops only what ODS Desktop started, removes its tasks and the
shortcuts it made (not ones you changed), and then shows each data folder with
its size and asks whether to delete it.

Chats are saved by the ODS window itself, as Microsoft Edge site data for
`localhost`, not in these folders. Reinstalling on the same port shows them
again. To erase them, delete the site data for `localhost` in Edge
(Settings > Cookies and site permissions).

## Living alongside other ODS installs

- **WSL Portal on the same PC:** Desktop never stops or replaces a model
  runtime a WSL Portal set up; setup stops and says so.
- **Docker-based ODS:** both can be installed. Desktop uses its own ports
  (the window tries 3101, then 13101 and 23101).

## Security

- Everything listens on `127.0.0.1`. The model server needs a key that only
  the app holds; the dashboard is served behind the same loopback rules
  nginx applies in the Docker stack, so another website cannot drive it.
- Downloads are pinned by size and SHA-256 (llama.cpp, Python, pip) or by the
  catalog's SHA-256 (the model). Python packages install from the hash-locked
  `requirements.lock`, wheels only. The native path does not use LiteLLM.
- Private files (keys, plans, the app environment) are readable by your
  account only.

## Troubleshooting

| Symptom | What to do |
|---|---|
| "This window is running as Administrator" | Open PowerShell normally (not "Run as administrator") and run setup again. |
| "The Microsoft Visual C++ runtime is not installed" | Let setup install it, or install it from https://aka.ms/vs/17/release/vc_redist.x64.exe and rerun. |
| "No free port for the ODS window" | Close the program using it, or rerun with `-Port <free port>`. |
| The model download stopped | Rerun setup; it resumes. |
| Smart App Control or antivirus blocked llama.cpp | Setup names the policy; allow the pinned llama.cpp files or rerun after a restart. |
| The window says "ODS is starting" | Normal for a few seconds after sign-in; it opens by itself. After two minutes it says so: restart the PC, or rerun setup. |
| The window says the model is still loading or stopped | Wait a minute after sign-in, or rerun setup. |

Logs: `%LOCALAPPDATA%\ODS\desktop\logs` (the app) and
`%LOCALAPPDATA%\ODS\lemonade\portal-runtime\llama-server.log` (the model).

## Known limits

- **CPU threads:** llama.cpp picks its own thread count. On PCs with many
  cores it can use more threads than is fastest; a `--threads` option needs a
  change to the shared llama.cpp launcher and is discussed in RFC #7968.
- **Context:** the CPU route uses up to 16K tokens. Older parts of a long chat
  stay saved and on screen; the model sees the most recent part, and the chat
  says so.
