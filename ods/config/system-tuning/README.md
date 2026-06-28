# System Tuning for Strix Halo

These files optimize the system for LLM inference on AMD Strix Halo.

## Apply all tuning (requires reboot for GRUB/modprobe):

```bash
# 1. Kernel boot parameters (GRUB)
# amd_iommu=off gives 2-6% improvement (iommu=pt does NOT give the same benefit)
sudo sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"/GRUB_CMDLINE_LINUX_DEFAULT="quiet splash amd_iommu=off"/' /etc/default/grub
sudo update-grub

# 2. AMD GPU module options
sudo cp amdgpu.conf /etc/modprobe.d/amdgpu.conf
sudo cp amdgpu_llm_optimized.conf /etc/modprobe.d/amdgpu_llm_optimized.conf
sudo update-initramfs -u

# 3. Memory tuning (applies immediately + persists)
sudo cp 99-ods.conf /etc/sysctl.d/99-ods.conf
sudo sysctl --system

# 4. Enable tuned for CPU governor optimization (5-8% prompt processing improvement)
sudo apt install tuned    # or: sudo dnf install tuned
sudo systemctl enable --now tuned
sudo tuned-adm profile accelerator-performance

# 5. Reboot for GRUB + modprobe changes
sudo reboot
```

## What each setting does:

### GRUB parameters
- `amd_iommu=off` — disable IOMMU for lower GPU memory access overhead (2-6% improvement)

### modprobe (amdgpu.conf)
- `ppfeaturemask=0xffffffff` — enable all power management features
- `gpu_recovery=1` — enable GPU hang recovery

### modprobe (amdgpu_llm_optimized.conf)
- `gttsize=120000` — allocate 120GB as GPU GTT memory (where HIP puts model weights)
- `pages_limit=31457280` — max 4KiB pages for GPU memory (120 GB)
- `page_pool_size=15728640` — pre-cache ~60GB for GPU usage (reduces allocation latency)

### sysctl (99-ods.conf)
- `vm.swappiness=10` — prefer keeping data in RAM (default 60 is too aggressive at swapping)
- `vm.vfs_cache_pressure=50` — keep directory/inode caches longer

### tuned (accelerator-performance)
- Sets CPU governor to `performance` (no power-saving throttling during inference)
- Disables CPU idle states for lowest latency
- 5-8% prompt processing improvement measured on Strix Halo
