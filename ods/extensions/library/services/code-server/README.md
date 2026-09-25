# code-server — browser project editor

Set `CODE_SERVER_PASSWORD` to a strong unique password in ODS Extensions, install and enable, then open `http://localhost:11030`. Authentication is required because the editor includes a terminal that executes commands inside its container.

The initial folder is `/home/coder/Playground`. Create a separate child directory per project, or clone your own repository into it using the integrated terminal. Open that directory in the editor. The persistent home also retains Git configuration, installed editor extensions and user settings. Container recreation does not discard those files.

This extension owns its workspace. The Portal agent's existing workspace is not automatically mounted here, and no host drive is exposed. Sharing a Portal project requires an explicit supported project association; do not pretend two identically named folders are the same filesystem. Git clone/push or deliberate file import/export can transfer work in the meantime.

## Runtime and permissions

- The official MIT-licensed 4.138.0 image is pinned by digest for Linux amd64 and arm64. Windows/macOS run it through Docker Desktop's Linux engine; Linux uses Docker Engine. No GPU or loaded model is required.
- A small image layer creates `Playground` and editor configuration directories with UID/GID 1000 ownership. The named home volume copies that initial ownership on first creation, avoiding OS-specific bind mounts.
- Runtime uses UID 1000 and `no-new-privileges`. The recipe invokes the editor through `dumb-init` directly, bypassing upstream's setuid `fixuid` startup path, which is unnecessary for this fixed-UID named volume. Arbitrary startup scripts from `ENTRYPOINTD` are not executed.
- No host Docker socket, host network, privileged mode or host filesystem is granted. The terminal can modify container/workspace files and access its network, but cannot administer the host Docker engine through this recipe. `sudo` escalation is blocked. Additional system toolchains should be added through a deliberate derived image rather than requiring runtime root.
- The UI binds to loopback; `CODE_SERVER_PORT` changes the host port while the internal listener stays on 8080. Password comes from the declared ODS secret. Rotate it in configuration and recreate the service; do not put it in project files or URLs.

## Development workflow

The upstream image includes editor, Git and basic shell utilities, not every project runtime. Check the actual tools required by your project before running it. Editor extensions come from the upstream configured gallery; availability and licenses can differ from Microsoft's marketplace. ODS does not count these editor plug-ins as separate ODS extensions.

For a development server running inside this container, use code-server's port forwarding/proxy feature. This is separate from Portal's published web preview. Do not claim a host URL is reachable merely because a process printed a container-local address. Remote editor access requires an intentional TLS reverse proxy with WebSocket support; keep password authentication enabled.

## Persistence and health

`code-server-home` stores project files, settings and potentially Git/SSH credentials. Back up it as sensitive data, with writes stopped for a consistent copy. Disabling preserves the volume. Moving to a new machine requires restoring that volume, not just reinstalling the image. Container-only system changes outside the home are lost on recreation.

The 2 GB memory and two-CPU limits cover the editor plus its child processes. Large language servers/builds may require deliberate tuning. The `/healthz` check verifies the editor listener. Its `expired` heartbeat value means no recent browser activity and is not treated as server failure. It does not verify a project's compilation, a forwarded preview or extension behavior.

Registry/source, authentication flags and health route were inspected. Schema, Compose and ODS staging validation do not establish that the image builds or browser editing works on every platform. Docker build, login, file persistence, terminal and proxy flows remain runtime checks.

- [Versioned source and license](https://github.com/coder/code-server/tree/v4.138.0)
- [Official image and user setup](https://github.com/coder/code-server/blob/v4.138.0/ci/release-image/Dockerfile)
- [Upstream entrypoint](https://github.com/coder/code-server/blob/v4.138.0/ci/release-image/entrypoint.sh)
- [Health route](https://github.com/coder/code-server/blob/v4.138.0/src/node/routes/health.ts)
