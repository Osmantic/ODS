# Blockbench for ODS

Browser-based low-poly modeling, texture painting and animation, based on upstream release v5.2.0 and commit `2569d0245d600030760cf0b8429b1b0017b871f1`: https://github.com/JannisX11/blockbench.

## Installation

Install `blockbench` from the ODS extension library and open its launch link, normally http://localhost:11076/. `BLOCKBENCH_PORT` selects another loopback port. No model or API key is required. The recipe builds the real upstream web target with its lockfile. It does not start Electron or install a desktop app on the host.

The first image build needs network access for npm packages and more memory than the lightweight static server's 256 MB runtime limit. Dependency lifecycle scripts are disabled during installation to avoid desktop native-addon setup; esbuild's own setup is explicitly rebuilt for bundling the web target.

## Project workflow

Create a model using the format appropriate to the intended game/application. Keep an editable `.bbmodel` source and its textures in `Playground/<project>` through normal project-file import/export. Export a target format supported by that project, such as glTF where offered by the chosen format, and have Portal use those actual exported assets. Animation/material support differs by exporter and target engine; retain the editable source rather than treating an exported mesh as a complete backup.

This web app uses browser file pickers/downloads and browser storage. It does not mount Portal's workspace or write directly into a Docker project volume. Explicitly save project files before clearing browser data or changing browser/profile/origin. Browser-based backups and settings are not a substitute for source files in the project.

## Platforms and external features

The static server is packaged for Linux amd64/arm64 through the respective ODS Docker adapters on Windows, Linux and macOS. Actual modeling uses the client browser's WebGL implementation and GPU; complex models can exceed a machine's practical capabilities. No CUDA requirement, server GPU reservation or chat-model context adjustment is introduced.

Upstream web features may contact external services for plugin listings/downloads, news, collaboration and other network features. Plugins and themes have their own licenses and capabilities and are not automatically installed or counted as ODS extensions. Do not assume desktop-only plugins or filesystem integrations work in the browser. This recipe adds no external account, session or model provider.

## Source and license

Upstream code is GPL-3.0-or-later. The complete pinned source archive, including original build scripts and lockfile, is available at `/source/blockbench-5.2.0.tar.gz`; the ODS recipe/build files are under `/source/ods-recipe/`. The original license is served at `/LICENSE.MD`. Retain corresponding source and dependency notices when redistributing the image. User-created models, textures and animation assets are not automatically relicensed by this packaging.

## Validation status

Schema, Compose and installation staging checks cannot prove browser behavior. Image build, rendering, import/export and recovery of saved files remain pending for user testing. The health probe checks only that the editor HTML is served. A useful manual check is to create and texture a small model, save it, reload the saved project and export it into the intended game pipeline.
