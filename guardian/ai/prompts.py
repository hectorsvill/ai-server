"""
Prompt templates for the AI Guardian reasoning system.
Highly specific to the vailab.us / ai-server stack.
"""
from __future__ import annotations

from guardian.core.config import cfg

# ── System Prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = f"""You are AI Guardian, an autonomous Linux server administrator managing the '{cfg.server.domain}' server.

## Server Identity
- Hostname: {cfg.server.hostname}
- Domain: {cfg.server.domain}
- Tailscale IP: {cfg.server.tailscale_ip}

## Infrastructure Stack
This server runs the following production services inside Docker containers via docker-compose:

1. **caddy** — HTTPS reverse proxy with Cloudflare DNS-01 TLS
   - Routes: webui.{cfg.server.domain}, docs.{cfg.server.domain}, dash.{cfg.server.domain},
             n8n.{cfg.server.domain}, crawler.{cfg.server.domain}
   - This is CRITICAL — if it goes down, ALL web services are unreachable

2. **open-webui** — LLM chat interface (port 3234 locally)
   - Depends on Ollama (native host service on port 11434)

3. **docmost** + **docmost_db** (PostgreSQL 16) + **redis** (7.2) — Wiki/docs platform
   - PostgreSQL and Redis are backing stores — treat with care, never auto-delete volumes

4. **n8n** — Workflow automation (port 5679 locally)

5. **glance** — Monitoring dashboard (port 11457 locally)
   - Uses rocm-stats native service for GPU metrics

## Native Host Services (NOT Docker)
- **ollama** (systemd) — LLM runtime on AMD RX 7900 GRE GPU (ROCm) — port 11434
- **rocm-stats** (systemd) — GPU metrics server — port 40404
- **tailscaled** (systemd) — VPN overlay for remote access — IP {cfg.server.tailscale_ip}

## Network Security Model
- All service ports are bound to 127.0.0.1 (localhost only)
- Only Caddy exposes ports 80/443 to the network
- UFW manages firewall rules (Docker can bypass UFW — check net bindings carefully)
- Tailscale provides authenticated remote access
- Cloudflare proxies public traffic to this server

## Your Role
You analyze system health metrics, Docker container status, and security events.
You propose specific, targeted actions to keep the server healthy and secure.
You NEVER delete Docker volumes, drop databases, or take irreversible actions without human approval.
You are cautious, precise, and always explain your reasoning.

## Response Format
Always respond with valid JSON in this exact structure:
{{
  "summary": "One-sentence summary of current server health",
  "health_score": 0-100,
  "issues": [
    {{
      "severity": "info|warning|critical",
      "category": "system|docker|security|network",
      "title": "Short issue title",
      "description": "Detailed explanation"
    }}
  ],
  "actions": [
    {{
      "action_type": "action_name",
      "target": "container_name or resource",
      "reason": "Why this action is needed",
      "risk_level": "low|medium|high|critical",
      "confidence": 0.0-1.0,
      "parameters": {{}}
    }}
  ],
  "reasoning": "Your step-by-step analysis",
  "confidence": 0.0-1.0
}}

Available action types:
- restart_container (target: container name) [low/medium risk]
- stop_container (target: container name) [medium risk]
- pull_image (target: container name) [low risk]
- prune_docker (type: images|containers|volumes) [low/medium risk — volumes=high]
- ban_ip (target: IP address, duration_minutes: N) [medium risk]
- run_security_update [medium risk — requires approval]
- clean_disk_space [low risk]
- restart_service (target: systemd unit name) [medium risk]
- alert_only (send notification without action) [no risk]
"""

# ── Analysis Prompt ───────────────────────────────────────────────────────────

def build_analysis_prompt(
    system_metrics: dict,
    docker_metrics: dict,
    security_metrics: dict,
    recent_events: list[dict],
    config_summary: str,
) -> str:
    import json

    # Compact representation of docker containers
    container_summary = []
    for name, info in docker_metrics.get("containers", {}).items():
        container_summary.append(
            f"  {name}: status={info['status']} health={info['health']} "
            f"restarts={info['restart_count']} cpu={info['cpu_pct']}% "
            f"mem={info['mem_mb']}MB"
            + (f" LOG_ERRORS={len(info['log_errors'])}" if info.get('log_errors') else "")
        )

    # Recent events summary (last 10)
    events_summary = []
    for ev in recent_events[-10:]:
        events_summary.append(f"  [{ev.get('severity','?').upper()}] {ev.get('title','?')}: {ev.get('description','')[:100]}")

    # SSH failures
    ssh_data = security_metrics.get("auth_log", {})
    ssh_failures = ssh_data.get("recent_failures", {})
    ssh_summary = "\n".join(f"  {ip}: {count} failures" for ip, count in ssh_failures.items()) or "  None"

    # System summary
    cpu = system_metrics.get("cpu", {})
    mem = system_metrics.get("memory", {})
    disks = system_metrics.get("disks", {})
    gpu = system_metrics.get("gpu", {})
    disk_summary = "\n".join(
        f"  {mount}: {info['pct']}% ({info['used_gb']}GB/{info['total_gb']}GB)"
        for mount, info in disks.items()
    )

    if gpu.get("available"):
        gpu_summary = (
            f"GPU use: {gpu.get('gpu_pct')}%  |  "
            f"VRAM: {gpu.get('vram_pct')}%  |  "
            f"Temp (junction): {gpu.get('temp_junc_c')}°C  |  "
            f"Power: {gpu.get('power_w')}W / {gpu.get('power_max_w')}W  |  "
            f"Clock: {gpu.get('sclk')}"
        )
    else:
        gpu_summary = f"unavailable ({gpu.get('error', 'unknown')})"

    return f"""## Current Server State — {cfg.server.hostname} ({cfg.server.domain})

### System Resources
CPU: {cpu.get('percent', '?')}%  |  Load: {cpu.get('load_avg', {}).get('1m', '?')} (1m)
RAM: {mem.get('pct', '?')}%  ({mem.get('used_gb', '?')}GB / {mem.get('total_gb', '?')}GB)  |  Swap: {mem.get('swap_pct', '?')}%
GPU (RX 7900 GRE): {gpu_summary}
Disk usage:
{disk_summary}

### Docker Containers
{chr(10).join(container_summary) or '  No containers found'}

### Docker Disk Usage
{json.dumps(docker_metrics.get('disk_usage', {}), indent=2)}

### SSH Brute Force Activity (last 10 min)
{ssh_summary}

### Suspicious Processes
{json.dumps(security_metrics.get('suspicious_processes', []), indent=2) or '  None detected'}

### Exposed Private Ports
{json.dumps(security_metrics.get('exposed_private_ports', []), indent=2) or '  None detected'}

### Recent Events (last 10)
{chr(10).join(events_summary) or '  No recent events'}

### Server Configuration Summary
{config_summary}

---
Analyze the above state, identify any issues, and propose appropriate actions.
Respond with the JSON format specified in your system prompt.
"""


def build_config_summary(project_dir: str) -> str:
    """
    Read key config files from the project directory and return a concise summary.
    This gives the AI context about the actual deployed configuration.
    """
    import os
    from pathlib import Path

    summary_parts: list[str] = []
    project = Path(project_dir)

    # docker-compose.yml — service list
    dc_path = project / "docker-compose.yml"
    if dc_path.exists():
        try:
            import yaml
            with open(dc_path) as f:
                dc = yaml.safe_load(f)
            services = list(dc.get("services", {}).keys())
            summary_parts.append(f"docker-compose services: {', '.join(services)}")
        except Exception:
            summary_parts.append("docker-compose.yml: (parse error)")

    # .env — non-secret keys
    env_path = project / ".env"
    if env_path.exists():
        try:
            env_keys = []
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key = line.split("=", 1)[0]
                        # Only include non-sensitive keys
                        if not any(s in key.upper() for s in
                                   ("SECRET", "TOKEN", "KEY", "PASSWORD", "PASS")):
                            env_keys.append(key)
            summary_parts.append(f".env keys (non-sensitive): {', '.join(env_keys)}")
        except Exception:
            pass

    # Caddyfile — routes
    caddyfile = project / "Caddyfile"
    if caddyfile.exists():
        try:
            content = caddyfile.read_text()
            # Extract subdomain lines
            routes = [line.strip() for line in content.splitlines()
                     if line.strip().endswith("{") and "." in line and not line.strip().startswith("#")]
            summary_parts.append(f"Caddy routes: {', '.join(routes[:10])}")
        except Exception:
            pass

    return "\n".join(summary_parts) if summary_parts else "Config files not readable"


# ── Security deep-dive prompt ─────────────────────────────────────────────────

def build_security_prompt(security_data: dict, recent_security_events: list[dict]) -> str:
    import json
    events_text = "\n".join(
        f"  [{e.get('severity','?').upper()}] {e.get('title','?')}: {e.get('description','')[:150]}"
        for e in recent_security_events[-20:]
    ) or "  None"

    return f"""## Security Analysis Request — {cfg.server.domain}

### SSH Activity
Recent failures by IP:
{json.dumps(security_data.get('auth_log', {}).get('recent_failures', {}), indent=2)}

### UFW Block Activity
{json.dumps(security_data.get('ufw', {}), indent=2)}

### Suspicious Processes
{json.dumps(security_data.get('suspicious_processes', []), indent=2)}

### Exposed Private Ports
{json.dumps(security_data.get('exposed_private_ports', []), indent=2)}

### Recent Security Events
{events_text}

Identify security threats, assess their severity, and propose defensive actions.
Respond with the JSON format specified in your system prompt.
"""
