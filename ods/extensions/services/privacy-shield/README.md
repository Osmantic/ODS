# Privacy Shield

API PII Protection for ODS

## Overview

Privacy Shield sits between your applications and the LLM API, automatically scrubbing Personally Identifiable Information (PII) before it leaves your server.

## Features

- **Automatic PII Detection**: Uses regex patterns to detect emails, phone numbers, SSNs, credit cards, IP addresses, and API keys
- **Anonymization**: Replaces PII with placeholders (e.g., `<PII_EMAIL_1>`, `<PII_PHONE_2>`)
- **Session Persistence**: Remembers PII-to-placeholder mappings per session for consistent responses
- **High Performance**: ~208 req/s throughput with caching
- **Zero Configuration**: Works out of the box with sensible defaults

### Detection Coverage

| Type | Detected | Notes |
|------|----------|-------|
| Email addresses | ✅ | Standard email formats |
| Phone numbers | ✅ | US formats with optional country code |
| SSN | ✅ | XXX-XX-XXXX format |
| Credit cards | ✅ | 16-digit card numbers |
| IP addresses | ✅ | IPv4 and IPv6 |
| API keys | ✅ | Common key patterns |
| **Person names** | ❌ | Not detected (requires NLP) |
| **Addresses** | ❌ | Not detected (requires NLP) |

> **Note:** Privacy Shield uses regex-based detection only. Person name and address detection would require NLP (e.g., Presidio or spaCy NER) — not currently integrated.

## Usage

### Enable Privacy Shield

Privacy Shield is included as a core service and starts automatically with the stack.

**Via Dashboard API:** the dashboard API requires a Bearer token — export `DASHBOARD_API_KEY` (found in your install's `.env`) before running these calls.

```bash
# Check status
curl -H "Authorization: Bearer $DASHBOARD_API_KEY" \
  http://localhost:3002/api/privacy-shield/status

# Enable
curl -X POST \
  -H "Authorization: Bearer $DASHBOARD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"enable": true}' \
  http://localhost:3002/api/privacy-shield/toggle

# Get stats
curl -H "Authorization: Bearer $DASHBOARD_API_KEY" \
  http://localhost:3002/api/privacy-shield/stats
```

### Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SHIELD_PORT` | 8085 | Port for Privacy Shield API |
| `TARGET_API_URL` | http://llama-server:8080/v1 | Upstream LLM API to proxy |
| `PII_CACHE_ENABLED` | true | Enable session PII caching |
| `PII_CACHE_SIZE` | 1000 | Max cached sessions |
| `PII_CACHE_TTL` | 300 | Session TTL in seconds |

### API Usage

Once enabled, route your LLM requests through Privacy Shield:

```python
# Instead of calling llama-server directly
response = requests.post(
    "http://localhost:8085/v1/chat/completions",  # Privacy Shield
    json={
        "model": "qwen2.5-32b-instruct",
        "messages": [{"role": "user", "content": "My email is john@example.com"}]
    }
)

# Response will have PII scrubbed:
# "Your email is <EMAIL_ADDRESS>"
```

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────┐
│  Your App   │────▶│Privacy Shield│────▶│   LLM   │
│             │◄────│  (PII Scrub) │◄────│         │
└─────────────┘     └──────────────┘     └─────────┘
                           │
                    ┌──────▼──────┐
                    │PII Cache/DB │
                    └─────────────┘
```

## Dashboard Integration

Privacy Shield status is displayed in the ODS dashboard:

- Service health indicator
- Enable/disable toggle
- Statistics (requests processed, PII items scrubbed)

## Files

- `proxy.py` — Main FastAPI proxy server
- `pii_scrubber.py` — PII detection and anonymization logic
- `Dockerfile` — Container definition
- `requirements.txt` — Python dependencies

## Performance

Tested on local hardware:
- **Throughput**: ~208 requests/second
- **Latency**: +2-5ms overhead vs direct API
- **Memory**: ~512MB with cache

## Security Notes

- PII is never logged to disk (only in memory cache)
- Cache is session-based and expires after TTL
- No PII leaves your server unencrypted
- Runs as non-root user in container

## Troubleshooting

**Privacy Shield not starting:**
```bash
# Check container status
docker compose ps privacy-shield

# View logs
docker compose logs privacy-shield
```

**PII not being scrubbed:**
- Verify Privacy Shield is enabled: `GET /api/privacy-shield/status`
- Check target API URL is correct
- Review logs for detection errors

## License

Part of ODS — Local AI Infrastructure
