# ComfyUI on a separate Windows GPU host

Run this path when a different device provides LiteLLM or other ODS services.
It installs only ComfyUI in the `ods-comfyui-standalone` Docker Compose project.
It does not install Portal, Pixel, a language model, or a dashboard. A full ODS
installation on the same Windows computer is left alone.

From the ODS source directory, in a normal PowerShell window:

```powershell
.\ods\installers\windows\install-comfyui-standalone.ps1 -DryRun
.\ods\installers\windows\install-comfyui-standalone.ps1
```

Use `-DataRoot C:\path\with\space\ComfyUI` to keep models and output on a
different local drive, or `-Port 8190` if 8188 is occupied. The installer checks
for an NVIDIA GPU, a local Docker Desktop Linux engine, GPU access from a CUDA
container, free disk, and a free loopback port. A CUDA/PyTorch build needs at
least 25 GiB free on the Docker Desktop and data drives before it begins.

After a healthy install, open `http://127.0.0.1:8188/` (or the chosen port).
The initial install contains no image checkpoint. Put a compatible model in
`<DataRoot>\models\checkpoints` before running an image workflow. The installer
does not download the large SDXL Lightning model automatically.

Models, input, output, saved workflows, UI settings, and custom nodes live under
`DataRoot`. Rerunning with the same root and port leaves a healthy container in
place. To stop only this installation, use:

```powershell
docker --context desktop-linux stop ods-comfyui-standalone
```

Rerunning the installer with the same data root and port restores the service.
No installer command deletes the data root. This standalone path currently
supports NVIDIA GPUs on Windows; it does not enable Dockerized ROCm on AMD.
