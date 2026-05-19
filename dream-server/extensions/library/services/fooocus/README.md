# Fooocus

User-friendly image generation UI built on Stable Diffusion. Provides an intuitive interface for generating high-quality images from text descriptions with minimal configuration.

## Requirements

- **GPU:** NVIDIA (min 8 GB VRAM)
- **Dependencies:** None

## Enable / Disable

```bash
dream enable fooocus
dream disable fooocus
```

Your data is preserved when disabling. To re-enable later: `dream enable fooocus`

## Access

- **URL:** `http://localhost:7865`

## First-Time Setup

1. Enable the service: `dream enable fooocus`
2. Open `http://localhost:7865`
3. Start generating images with natural language prompts

First startup may download several GB of model files. Subsequent starts are instant.
