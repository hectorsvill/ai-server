# UFW Firewall Guide

UFW (Uncomplicated Firewall) is the firewall layer protecting this server. This guide covers the current rule set, the Docker bypass problem, and how to manage rules going forward.

---

## Current Rule Set

```
[ 1] 22195/tcp    ALLOW IN    Anywhere    # Custom SSH
[ 2] 80/tcp       ALLOW IN    Anywhere    # HTTP (Caddy → redirects to HTTPS)
[ 3] 443/tcp      ALLOW IN    Anywhere    # HTTPS (Caddy → all services)
[ 4] 11434/tcp    ALLOW IN    172.19.0.0/16  # Ollama — Docker containers only
```

All public-facing services are accessed exclusively through Caddy at `https://*.vailab.us`. Direct port access to Open WebUI, Docmost, and Glance is blocked.

---

## Why Docker Bypasses UFW

Docker writes its own `iptables` rules directly, bypassing UFW entirely. When a port is published in `docker-compose.yml` like this:

```yaml
ports:
  - "4389:3000"
```

Docker opens that port to the world regardless of UFW rules. Deleting the UFW rule has no effect.

**The fix:** bind published ports to `127.0.0.1` so Docker only listens on the loopback interface:

```yaml
ports:
  - "127.0.0.1:4389:3000"
```

This makes the port reachable from the host machine only — not from the LAN or internet. Caddy still reaches the container via the internal `ai-network` Docker bridge (using container names, not host ports), so nothing breaks.

This is already applied to `open-webui`, `glance`, and `docmost` in `docker-compose.yml`.

---

## Ollama and the Docker Network

Ollama runs natively on the host (not in Docker) and must be reachable by the `open-webui` container via `host.docker.internal:11434`. This traffic originates from the `ai-network` subnet (`172.19.0.0/16`) and passes through UFW's INPUT chain.

Because of this, Ollama needs a UFW rule — but scoped only to the Docker subnet, not to the whole internet:

```bash
sudo ufw allow from 172.19.0.0/16 to any port 11434 comment "Ollama - Docker containers only"
```

This is already in place. Do **not** open 11434 to `Anywhere`.

---

## Common UFW Commands

```bash
# View all rules with index numbers
sudo ufw status numbered

# Add a rule
sudo ufw allow 443/tcp
sudo ufw allow from 192.168.1.0/24 to any port 22195

# Delete a rule by number (check numbers first with 'status numbered')
sudo ufw delete 3

# Delete a rule by specification
sudo ufw delete allow 443/tcp

# Enable / disable UFW
sudo ufw enable
sudo ufw disable

# Reset all rules (destructive — will lock you out if SSH rule is missing)
sudo ufw reset
```

> **Warning:** Always confirm your SSH rule exists before running `ufw reset` or deleting rules. If you lock yourself out over the network, you'll need physical/console access to recover.

---

## Adding a New Service

If you add a new service that should only be accessible via Caddy:

1. Bind its port to `127.0.0.1` in `docker-compose.yml` — do **not** add a UFW rule.
2. Add a reverse proxy block in the `Caddyfile`.
3. Rebuild/restart Caddy: `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`

If the service needs to be reachable from your LAN directly (e.g. a non-HTTP service):

```bash
sudo ufw allow from 192.168.1.0/24 to any port <PORT> comment "description"
```

---

## Verifying the Setup

After any firewall or Docker change, verify from another machine on your LAN:

```bash
# Should time out — direct port access is blocked
curl --connect-timeout 5 http://192.168.1.83:4389

# Should work — Caddy HTTPS is open
curl -I https://wiki.vailab.us
```
