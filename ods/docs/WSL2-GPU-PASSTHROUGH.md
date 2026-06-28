# WSL2 GPU Passthrough for Running Local AI Models

## 1. Prerequisites

### Windows Version
Ensure you are running **Windows 10 version 2004 or later** (Build 19041) or **Windows 11**.

### GPU Drivers
- Install the latest NVIDIA drivers from the [NVIDIA website](https://www.nvidia.com/Download/index.aspx).
- Ensure the drivers support WSL 2 GPU passthrough. You can check compatibility on the NVIDIA Driver Downloads page.

### WSL2 Version
Make sure WSL2 is installed and updated:
```bash
wsl --update
```

## 2. Step-by-Step Enable GPU Passthrough for WSL2

### Install WSL2 and a Linux Distribution
If not already installed, install WSL2 and a Linux distribution (e.g., Ubuntu):
```bash
wsl --install -d Ubuntu
```

### Set WSL2 as the Default Version
Ensure WSL2 is set as the default version:
```bash
wsl --set-default-version 2
```

### Install the NVIDIA Container Toolkit
Inside your WSL2 distribution, install the NVIDIA Container Toolkit:
```bash
sudo apt-get update
sudo apt-get install -y nvidia-driver-510
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

### Enable GPU Support in WSL2
Edit the `.wslconfig` file in your Windows user directory (`C:\Users\<YourUsername>`):
```ini
[wsl2]
gpu=true
memory=8GB  # Adjust as needed
processors=4  # Adjust as needed
```

Restart WSL2:
```bash
wsl --shutdown
wsl --distribution Ubuntu
```

## 3. Verifying GPU is Visible Inside WSL2

Run `nvidia-smi` to verify the GPU is recognized:
```bash
nvidia-smi
```
You should see output similar to this:
```
+-----------------------------------------------------------------------------+
| NVIDIA-SMI 510.85.02    Driver Version: 510.85.02    CUDA Version: 11.6     |
|-------------------------------+----------------------+----------------------+
| GPU  Name        Persistence-M| Bus-Id        Disp.A | Volatile Uncorr. ECC |
| Fan  Temp  Perf  Pwr:Usage/Cap|         Memory-Usage | GPU-Util  Compute M. |
|                               |                      |               MIG M. |
|===============================+======================+======================|
|   0  NVIDIA GeForce GTX 165... Off  | 00000000:01:00.0 Off |                  N/A |
| N/A   41C    P8     9W / 175W |      0MiB /  4096MiB |      0%      Default |
|                               |                      |                  N/A |
+-----------------------------------------------------------------------------+
```

## 4. Common Issues and Fixes

### Driver Version Mismatch
Ensure that the NVIDIA drivers installed on Windows match those expected by the NVIDIA Container Toolkit in WSL2. Reinstall the drivers if necessary.

### CUDA Not Found
Install CUDA in your WSL2 distribution if it's not already installed:
```bash
sudo apt-get install -y cuda
```
Verify the installation by checking the CUDA version:
```bash
cuda --version
```

## 5. Performance Expectations vs Native Linux
Running AI models in WSL2 with GPU passthrough offers significant performance benefits compared to CPU-only execution. However, there may still be some overhead compared to native Linux due to the virtualization layer. For most practical purposes, the performance should be close to native Linux.

This guide should help you set up and verify GPU passthrough for WSL2, enabling you to run local AI models efficiently on your Windows machine.