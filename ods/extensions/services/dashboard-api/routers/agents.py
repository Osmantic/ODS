"""Agent monitoring endpoints."""

import html as html_mod

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from agent_monitor import get_full_agent_metrics, cluster_status, throughput
from security import verify_api_key

router = APIRouter(tags=["agents"])


@router.get("/api/agents/metrics")
async def get_agent_metrics(api_key: str = Depends(verify_api_key)):
    """Get comprehensive agent monitoring metrics."""
    return get_full_agent_metrics()


@router.get("/api/agents/metrics.html")
async def get_agent_metrics_html(api_key: str = Depends(verify_api_key)):
    """Get agent metrics as HTML fragment for htmx."""
    metrics = get_full_agent_metrics()
    cluster = metrics.get("cluster", {})
    agent = metrics.get("agent", {})
    tp = metrics.get("throughput", {})

    cluster_class = "status-ok" if cluster.get("failover_ready") else "status-warn"
    failover_text = "Ready \u2705" if cluster.get("failover_ready") else "Single GPU \u26a0\ufe0f"
    last_update = agent.get("last_update", "")
    last_update_time = last_update.split("T")[1][:8] if "T" in last_update else "N/A"

    # Escape all interpolated values for HTML safety
    def esc(value):
        return html_mod.escape(str(value))

    active_gpus = esc(cluster.get("active_gpus", 0))
    total_gpus = esc(cluster.get("total_gpus", 0))
    failover_safe = esc(failover_text)
    sessions = esc(agent.get("session_count", 0))
    last_update_safe = esc(last_update_time)
    tp_current = esc(f"{tp.get('current', 0):.1f}")
    tp_average = esc(f"{tp.get('average', 0):.1f}")

    html = f"""
    <div class="grid">
        <article class="metric-card">
            <div class="metric-label">Cluster Status</div>
            <div class="metric-value {cluster_class}">{active_gpus}/{total_gpus} GPUs</div>
            <p style="margin: 0; font-size: 0.875rem;">Failover: {failover_safe}</p>
        </article>
        <article class="metric-card">
            <div class="metric-label">Active Sessions</div>
            <div class="metric-value">{sessions}</div>
            <p style="margin: 0; font-size: 0.875rem;">Updated: {last_update_safe}</p>
        </article>
        <article class="metric-card">
            <div class="metric-label">Throughput</div>
            <div class="metric-value">{tp_current}</div>
            <p style="margin: 0; font-size: 0.875rem;">tokens/sec (avg: {tp_average})</p>
        </article>
    </div>
    """
    return HTMLResponse(content=html)


@router.get("/api/agents/cluster")
async def get_cluster_status(api_key: str = Depends(verify_api_key)):
    """Get cluster health and node status."""
    await cluster_status.refresh()
    return cluster_status.to_dict()


@router.get("/api/agents/throughput")
async def get_throughput(api_key: str = Depends(verify_api_key)):
    """Get throughput metrics (tokens/sec)."""
    return throughput.get_stats()
