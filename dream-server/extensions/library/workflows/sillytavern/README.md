# SillyTavern Workflows

## Overview

SillyTavern is a character-based roleplay chat interface. Unlike API-first services, SillyTavern is primarily a UI tool that users interact with directly through their browser.

## Available Workflows

### status-check.json

**Purpose:** Monitor SillyTavern service availability

**What it does:**
- Checks if the SillyTavern web UI is responding
- Reports status to Discord

**Webhook:** `GET /sillytavern-status`

**Usage:**
1. Import the workflow into n8n
2. Trigger via webhook or schedule
3. Receive Discord notification with current status

## SillyTavern Usage

SillyTavern is accessed directly via web browser:

1. Enable the extension: `dream enable sillytavern`
2. Access at: `http://localhost:8001` (or your configured port)
3. Configure your AI backend in the SillyTavern UI
4. Create or import character cards
5. Start roleplay conversations

## Integration Notes

- SillyTavern connects to Dream Server's LLM endpoint automatically via `LLM_API_URL`
- No GPU required (CPU-only service)
- Data persists in `./data/sillytavern/`
- Configuration stored in `./config/sillytavern/`

## Resources

- [SillyTavern Documentation](https://docs.sillytavern.app/)
- [Character Cards Repository](https://www.characterhub.org/)
