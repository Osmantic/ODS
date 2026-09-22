# Redis — In-Memory Key-Value Store & Message Broker

An optional, secured Redis 7.4 in-memory data store with persistent Append-Only File (AOF) storage. Use it for local AI caching (semantic prompt/response caching, token counter caching), background task distribution (Celery, BullMQ, RQ), rate limiting, and multi-agent pub/sub messaging.

## Setup

Set credentials in your ODS `.env` file before enabling **Redis (In-Memory Cache & Broker)**:

- `REDIS_PASSWORD`: administrative authentication token (minimum 8 characters).
- `REDIS_PORT`: published host port (defaults to `6380` to prevent collisions with any native host Redis running on `6379`).

If absent, running `setup.sh` automatically provisions a secure random 32-character password into `.env`. Compose refuses missing or empty passwords.

Once enabled:
- **Host Endpoint**: `redis://:<password>@localhost:6380/0`
- **Internal Network Endpoint**: `redis://:<password>@redis:6379/0` (for services attached to `ods-network`)

## Settings

| Variable | Default | Description |
| --- | --- | --- |
| `REDIS_PORT` | `6380` | Published Redis port on host (internal listener remains `6379`) |
| `REDIS_PASSWORD` | required | Master password required for all Redis commands via `AUTH` |

The default binding is loopback (`127.0.0.1`). Container privilege escalation is restricted via `no-new-privileges:true`.

## Usage & Client Integration

### Python (`redis-py`)

```python
import redis

client = redis.Redis(
    host="localhost",
    port=6380,
    password="<YOUR_REDIS_PASSWORD>",
    decode_responses=True,
)

# Cache an LLM prompt response
client.setex("cache:prompt:hash_xyz", 3600, "Cached completion output")
result = client.get("cache:prompt:hash_xyz")
```

### CLI Inspection

```bash
docker exec -it ods-redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli info memory'
```

## Data and Persistence

Redis operates in Append-Only File mode (`--appendonly yes`), persisting transactional operations under `./data/redis` on the host. Stopping or upgrading the container retains cached records and broker queues.

References: [Redis Documentation](https://redis.io/docs/), [Redis Security Best Practices](https://redis.io/docs/management/security/).
