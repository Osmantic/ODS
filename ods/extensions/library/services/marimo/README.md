# marimo reactive Python notebooks

An optional local editor for notebooks stored as ordinary Python files. marimo
tracks dependencies between cells and recomputes dependent results. This is
useful for reproducible data analysis and version-controlled Python experiments.
It uses a different notebook/runtime format from Jupyter; enabling it does not
convert or replace existing notebooks.

## Start and sign in

Set MARIMO_PASSWORD to a strong password of at least 16 characters, with no
surrounding whitespace or line breaks, then install and enable the extension.
Open http://localhost:2718 and enter that password on the login page. Use
MARIMO_PORT for a different local port. Do not put the password in a URL.
The server reads it from a private temporary file; startup URLs are suppressed
so they cannot print the token. A restart invalidates existing browser sessions.

This is one trusted operator's code-execution workspace, not a multi-user
authorization system. Anyone signed in can run Python with the container user's
access to mounted notebooks, configuration and the ODS network. Review notebooks
before running them. Bind to loopback unless you deliberately configure a
protected remote route and TLS. No model provider, cloud account, MCP server or
document ingestion is configured automatically.

## Files and dependencies

Notebooks and selected datasets live in ods/data/marimo/workspace, mounted at
/workspace. User configuration and caches live in ods/data/marimo/home. ODS
prepares both directories for the image's UID 1000. The container runs without
root privileges, with a read-only image and bounded temporary storage.

The slim runtime includes marimo and its core dependencies. It does not include
a general data-science distribution. Automatic per-notebook uv environments are
disabled; marimo's --no-sandbox option controls dependency management, not the
Docker isolation boundary. For extra packages, maintain a pinned derived image
and rebuild it deliberately. Interactive installs into the read-only system
environment will not work. Python standard-library notebooks work offline
after the initial image build.

The published 0.24.0 image is pinned as the build base and upgraded to Python
package 0.24.2, including the newer cookie-token handling. No 0.24.2 container
tag was available when this definition was prepared. The build checks package
compatibility. Review the resulting build and dependency updates before upgrades.

The existing install setup hook builds the local ods-marimo:0.24.2-r1 image.
Compose consumes that image with pull_policy: never, so activation and
disable/re-enable follow ODS's image-only user-extension policy. Installation
requires working Docker build access and initial image/package network access;
a failed build fails setup visibly. Retain this local image for offline use and
rollback. If an operator removes it, rerun the installed setup.sh before starting
the service. Changing an image version requires updating both setup.sh and the
Compose image reference.

Version checks and built-in sharing UI are disabled. These settings do not
prevent trusted notebook code from making network requests.

## Recovery and validation

Stop the editor and back up both directories plus private deployment
configuration. Recreate it with the same image to verify that notebooks and
configuration remain usable. For rollback after an upgrade, retain the prior
image and matching copies of both directories. Reverting the definition does
not delete notebooks.

The health endpoint proves server availability only. The opt-in
ODS_TEST_MARIMO=1 pytest ods/tests/test_marimo.py run builds the image, checks
authentication over real HTTP, executes an out-of-order dependency notebook
through marimo's export command and checks its persistence after recreation.
It uses synthetic files and owned containers/volumes with no external network.
Browser editing, WebSocket reconnection, native ARM/macOS/Windows, large
notebooks, custom packages and full ODS install/update/doctor remain separate
validation targets.

Sources: [container packaging](https://github.com/marimo-team/marimo/blob/0.24.2/docker/Dockerfile),
[authentication](https://docs.marimo.io/guides/deploying/authentication/),
[0.24.2 release](https://github.com/marimo-team/marimo/releases/tag/0.24.2).
