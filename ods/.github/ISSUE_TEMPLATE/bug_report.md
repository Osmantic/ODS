---
name: Bug Report
about: Something isn't working as expected
labels: bug
---

**Hardware**
- GPU: (e.g., RTX 4090 24GB, Strix Halo 96GB, none)
- RAM:
- OS: (e.g., Ubuntu 24.04, Windows 11 + WSL2, macOS 15)
- Tier: (e.g., 2, SH_LARGE)

**What happened?**
A clear description of the bug.

**What did you expect?**
What should have happened instead.

**Steps to reproduce**
1.
2.
3.

**Logs**
```
Paste relevant output from:
  docker compose logs <service> | tail -50
  cat /tmp/ods-install.log | tail -50
```

**Installer version**
```
grep VERSION installers/lib/constants.sh
```
