# LAN Cluster Guide

Pool GPUs across multiple machines for distributed inference. One machine acts as the **controller** (runs llama-server), the others are **workers** (run rpc-server to share their GPU). All machines need DreamServer installed.

## Architecture

```
Controller (llama-server)          Worker 1 (rpc-server)
  GPU: RTX 4090                      GPU: RTX 3090
  Port: 8080 (API)                   Port: 50052 (RPC)
  Port: 50051 (setup listener)       |
        |                            |
        +------- LAN ----------------+
        |                            |
  Port: 50053 (UDP discovery)    Worker 2 (rpc-server)
                                     GPU: RX 7900 XTX
                                     Port: 50052 (RPC)
```

The controller runs llama-server with `--rpc` flags pointing to each worker. Workers run rpc-server which exposes their GPU over the network. llama.cpp handles splitting the model across all GPUs.

## Requirements

- All machines on the same network (can reach each other by IP)
- DreamServer installed on every machine
- During install, select Custom and enable LAN cluster mode (builds Docker images)
- TCP port 50051 open on controller (join handshake)
- TCP port 50052 open on workers (RPC traffic)
- UDP port 50053 open on both (auto-discovery — optional)

## Ports Summary

| Port  | Protocol | Where      | Purpose                    |
|-------|----------|------------|----------------------------|
| 8080  | TCP      | Controller | llama-server API           |
| 50051 | TCP      | Controller | Setup listener (join)      |
| 50052 | TCP      | Workers    | rpc-server (GPU sharing)   |
| 50053 | UDP      | Both       | Auto-discovery beacon      |
| 50054 | TCP      | Workers    | Agent status endpoint      |

## Quick Start

### Controller

```bash
dream cluster enable    # detect GPU, generate token, pick network interface
dream cluster setup     # listen for workers (keep this running)
```

`enable` outputs a token and the controller IP. Copy both.

### Workers

**Agent flow (recommended)** — the agent monitors rpc-server and restarts it if it crashes:

```bash
dream cluster agent start --token <TOKEN> --controller <CONTROLLER_IP>
```

**Manual flow** — one-shot, no monitoring:

```bash
dream cluster join <CONTROLLER_IP> --token <TOKEN>
```

### Accept and Finish

Back on the controller terminal, each worker appears with its GPU info. Type `y` to accept.

When all workers have joined, press Ctrl+C. It will offer to restart llama-server with the new cluster configuration.

### Verify

```bash
dream cluster status    # shows all nodes, online/offline, ping
dream cluster test      # 10 TCP pings per worker
```

The dashboard also shows a "LAN Cluster" page with live node status when cluster mode is enabled.

---

## Detailed Setup

### Step 1: Enable Cluster Mode (Controller)

```bash
dream cluster enable
```

This will:
- Detect the controller's GPU (name, VRAM, backend)
- Generate a security token (`dream_...`)
- If multiple network interfaces exist, ask you to pick one
- Save config to `config/cluster.json`
- Set `CLUSTER_ENABLED=true` in `.env`

Output shows the token and next steps. You can also pass `--interface=IP` to skip the interactive picker.

### Step 2: Start Setup Listener (Controller)

```bash
dream cluster setup
```

This starts:
- A TCP listener on port 50051 for join requests
- A UDP broadcast beacon on port 50053 for auto-discovery

The terminal shows the controller IP, token, and exact commands to run on workers. Leave it running until all workers have joined.

### Step 3: Start Workers

#### Option A: Agent Flow (Recommended)

The agent is a background process that handles the full worker lifecycle:

```bash
dream cluster agent start --token <TOKEN> --controller <CONTROLLER_IP>
```

What the agent does:
1. Connects to the controller's setup listener (TCP 50051)
2. Sends a join request with token, GPU info, and RPC port
3. Waits for the operator to accept on the controller
4. Starts the rpc-server Docker container
5. Monitors rpc-server every 10s — restarts it if it crashes

Check agent status and logs:
```bash
dream cluster agent status
dream cluster agent logs
```

#### Option B: Manual Join (One-Shot)

```bash
dream cluster join <CONTROLLER_IP> --token <TOKEN>
```

This connects, sends the join request, starts rpc-server, and exits. rpc-server keeps running but is not monitored — if it crashes, you must restart it manually.

### Step 4: Accept Workers (Controller)

Each worker that connects shows up on the controller terminal:

```
  New worker wants to join:
    IP:       192.168.1.101
    RPC port: 50052
    Backend:  nvidia
    GPU:      NVIDIA GeForce RTX 3090 (24.0 GB)

  Accept this worker? [Y/n]
```

Type `y` to accept. The worker is added to `config/cluster.json`.

### Step 5: Finish and Restart

Press Ctrl+C on the controller's setup terminal. It asks:

```
  Restart llama-server now to apply? [Y/n]
```

Say yes. llama-server restarts with `--rpc` flags pointing to all accepted workers.

---

## Auto-Discovery

When `dream cluster setup` runs, it broadcasts a UDP beacon every 5 seconds on port 50053. Workers using `--discover` or the agent (without `--controller`) listen for this beacon to find the controller automatically.

**When auto-discovery works:**
- Machines on the same LAN segment / broadcast domain
- Most home networks with wired connections

**When auto-discovery does NOT work:**
- Cloud VPCs (AWS, GCP, Azure) — broadcast is blocked at the network fabric
- WiFi with AP/client isolation enabled
- Machines on different subnets or VLANs
- Some managed enterprise networks

**If discovery fails**, use `--controller IP` to connect directly. This always works as long as TCP connectivity exists.

---

## Network Interface Selection

If a machine has multiple network interfaces (e.g. LAN + VPN + Docker bridge), you want cluster traffic on the right one.

**Controller**: `dream cluster enable` detects multiple interfaces and asks you to pick. The choice is saved as `CLUSTER_INTERFACE` in `.env` and used by the discovery beacon and setup listener.

**Worker**: Pass `--interface IP` to bind discovery to a specific interface:

```bash
dream cluster agent start --token T --controller IP --interface 192.168.1.50
dream cluster join --discover --token T --interface 192.168.1.50
```

---

## Fault Tolerance

### Worker Crashes

llama.cpp RPC has no built-in fault tolerance — if a worker disconnects mid-inference, llama-server crashes (exit 134 / SIGABRT).

The **supervisor** on the controller handles this:
1. Detects llama-server crash
2. TCP-pings all workers to find which are alive
3. Restarts llama-server with only the live workers (degraded mode)
4. Inference continues on remaining GPUs

The **agent** on the worker handles its side:
- Detects rpc-server container stopped
- Restarts rpc-server automatically

### Worker Recovery

The supervisor's watchdog monitors dead workers every 5 seconds. When a previously dead worker comes back online, it triggers a graceful restart to re-add it. The cluster returns to full capacity.

### Network Partition

If a worker becomes unreachable AND llama-server stops responding to `/health` (hung on a dead socket), the watchdog kills the hung process. The supervisor restarts in degraded mode with only reachable workers.

The watchdog never kills a responsive llama-server — only one that's actually hung.

### All Workers Dead

If every worker is unreachable, the supervisor waits 15 seconds, then starts llama-server with the controller's GPU only (no `--rpc`). When workers come back, they're re-added automatically.

### Tensor Caching

rpc-server runs with `-c` (tensor caching) enabled by default. After the first model load, subsequent restarts are fast because tensor data is cached on the worker using FNV-1a hashes.

---

## Managing the Cluster

### Check Status
```bash
dream cluster status    # nodes, connectivity, VRAM pool, tensor split
dream cluster test      # 10 TCP pings per worker with min/avg/max latency
```

### Add More Workers Later
```bash
dream cluster setup     # on controller — starts listening again
# then on new worker:
dream cluster agent start --token <TOKEN> --controller <CONTROLLER_IP>
```

### Remove a Worker
```bash
# On controller:
dream cluster remove <WORKER_IP>
dream restart llama-server

# On worker:
dream cluster agent stop
dream cluster leave
```

### Stop the Agent (Worker Keeps Running)
```bash
dream cluster agent stop    # rpc-server container stays up
```

### Leave the Cluster Completely
```bash
dream cluster leave         # stops rpc-server and removes container
```

### Disable Cluster Mode
```bash
dream cluster disable       # on controller — clears all config
dream restart llama-server
```

---

## Agent Persistence

The agent does not auto-start on reboot. To enable that:

```bash
systemctl --user enable dream-cluster-agent.service
```

View agent logs:

```bash
dream cluster agent logs                                    # nohup fallback
journalctl --user -u dream-cluster-agent -f                 # systemd
```

---

## Security

llama.cpp RPC has **no encryption** and **no authentication**. All tensor data (model weights, activations) flows in plaintext. The cluster token only protects the join handshake, not ongoing RPC traffic.

- Only run on trusted networks
- For untrusted networks, use a WireGuard or Tailscale tunnel and use the tunnel IPs

---

## Troubleshooting

**Agent starts but controller doesn't see it:**
- Is `dream cluster setup` running on the controller?
- Check agent logs: `dream cluster agent logs`
- Verify TCP 50051 is open on controller: `nc -zv <CONTROLLER_IP> 50051`

**Auto-discovery not working:**
- Test manually: `python3 scripts/cluster_discovery.py discover 10`
- If no response, broadcast is blocked. Use `--controller IP` instead.
- Check UDP 50053 is open: `sudo firewall-cmd --add-port=50053/udp`

**rpc-server starts but llama-server can't connect:**
- Check TCP 50052 is open on worker: `nc -zv <WORKER_IP> 50052`
- Check rpc-server is running: `docker ps | grep rpc-server`
- Check rpc-server logs: `docker logs dream-rpc-server`

**llama-server crashes on worker disconnect:**
- This is expected (llama.cpp limitation). The supervisor auto-restarts in degraded mode.
- Check supervisor logs: `docker logs dream-llama-rpc-server`

**Token mismatch:**
- Controller token: `grep CLUSTER_TOKEN dream-server/.env`
- Worker token: `cat dream-server/config/cluster-agent.json`
- Re-run agent with correct token: `dream cluster agent start --token <CORRECT_TOKEN> --controller <IP>`
