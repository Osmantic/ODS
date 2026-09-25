# CyberChef — local data workbench

Install and enable in ODS Extensions, then open `http://localhost:11028`. The official Apache-2.0 application combines operations such as Base64 decoding, hashes, date conversions, decompression and binary inspection into reusable recipes. No model, GPU, API subscription or account is needed.

## Using it with project data

Paste an input or load a file, add operations to the recipe, then export the output to your project directory. For a small initial check, use `SGVsbG8=` with **From Base64**; the expected result is `Hello`. Save the recipe separately if it belongs with the project. ODS does not silently read workspace files or give the browser write access to the entire workspace.

Processing occurs in the browser. Saved recipes use browser local storage, not a server database. Export recipes/output before clearing browser data, changing profiles or moving computers. Changing the launch origin/port also changes the browser storage scope. Shared URLs can include input and recipe contents; review them before sharing. Explicit networking operations can contact their configured destinations, so local hosting does not make every possible recipe network-isolated.

## Deployment contract

- Official `11.5.0` image pinned by digest, using upstream unprivileged Nginx UID 101. Registry platforms include amd64, arm64 and ARMv7. Windows/macOS use Linux containers in Docker Desktop; Linux uses Docker Engine.
- Host UI binds to `127.0.0.1:11028`; `CYBERCHEF_PORT` changes only that host port. The container listens on 8080. No Docker socket, host directory, model route or secret is injected.
- There is deliberately no data volume: the container serves static assets. Stopping/recreating it preserves browser-local data, but is not a backup of that data.
- Container memory is limited to 256 MB for serving files. Large inputs consume memory and CPU on the browser's computer; the container limit does not constrain browser workloads. Disable automatic processing for expensive recipes when appropriate.
- The HTTP health check confirms that static assets can be served, not that every operation or downloaded browser worker works. Operation checks, browser compatibility and output validation remain runtime work.

## Evidence

The release, license, official Dockerfile, registry architecture manifests and nonroot image user were inspected. Schema, Compose and ODS installation-staging validation are separate from real application testing. No CyberChef container or browser workflow was started during preparation.

- [Versioned source and license](https://github.com/gchq/CyberChef/tree/v11.5.0)
- [Official deployment image](https://github.com/gchq/CyberChef/blob/v11.5.0/Dockerfile)
- [Usage and local processing](https://github.com/gchq/CyberChef#how-it-works)
