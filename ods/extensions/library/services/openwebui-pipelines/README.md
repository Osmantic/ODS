# Open WebUI Pipelines — isolated Python processing API

MIT project, pinned official image at source revision `039f9c54f8e9f9bcbabde02c2c853e80d25c79e4`. The image metadata dates to **2025-08-18**; this is not advertised as a new 2026 release. Upstream: https://github.com/open-webui/pipelines/tree/039f9c54f8e9f9bcbabde02c2c853e80d25c79e4.

Pipelines is a separate Python execution/API framework. It is useful when custom processing should run outside the main chat server. Upstream recommends Open WebUI's built-in Functions for simple provider connections or basic filters; this service is not required for every chat. It does not include a new chat UI or an inference model.

## Connect explicitly

Set required `OPENWEBUI_PIPELINES_API_KEY` to 64 random hexadecimal characters. The upstream shared default key is rejected. The host API is `http://localhost:11109`; for an Open WebUI container on `ods-network`, use `http://openwebui-pipelines:9099` and the configured key in its Connections settings. For an OpenAI-compatible client, use the `/v1` base path. Filter pipelines additionally need a client supporting Pipelines hooks.

The initial `/v1/models` list is empty because no pipeline code is seeded. Add a reviewed, concrete pipeline through the native authenticated management API or the Open WebUI administrator interface. This recipe never downloads example pipelines, resets your pipeline directory or routes Portal traffic automatically. Installing the service does not alter the selected model, provider URL or context limit.

Pipeline code executes Python with the service's permissions and can make network requests. The API key grants management capability, not just read access: do not distribute it as a public end-user token. Review pipeline provenance and dependency pins before installation. Native frontmatter requirements can install Python packages into `/data/python`; these depend on the specific pipeline and are not silently represented as part of the pinned base image. No pipeline-specific CUDA/ROCm/Metal support is promised by this CPU recipe.

## Persistence and limits

The `openwebui-pipelines-data` volume persists `/data/pipelines` (code/configuration), `/data/python` (user Python packages) and cache. Back it up with the API secret separately, and preserve UID/GID 1000 ownership when restoring. Rotating the environment key requires updating clients and recreating the container; it does not delete pipelines.

The native application is started directly with Uvicorn, avoiding upstream startup download/reset steps and blanket trust of forwarded proxy headers. Nonroot/read-only root, bounded temporary files, two CPUs and two GiB memory; pipelines may need more resources and must be sized individually. The API is published only on loopback and is also reachable by ODS network peers. No Docker socket or host filesystem mount is supplied. Use deliberate HTTPS/authentication routing before remote access.

## Platforms and verification

Pinned official CPU image includes Linux amd64/arm64 for Docker on Windows/Linux/macOS. Python/native dependencies requested by a particular pipeline must support that architecture independently. Open WebUI is optional, not installed automatically. The Docker probe authenticates to the native model-list endpoint; an empty list means no pipeline installed, not failed health.

Image build, API authentication, installation/execution of a real reviewed pipeline, client compatibility and restart/platform validation remain pending. No service/model or third-party pipeline was executed during recipe preparation.
