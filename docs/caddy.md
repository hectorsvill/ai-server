# Caddy Reference

Day-to-day reference for Caddy in this stack. For first-time setup, see [`https-setup.md`](https-setup.md). For how Caddy fits into the architecture, see [`SERVICES.md`](SERVICES.md).

---

## What this document covers

- Caddyfile explained line by line
- How Caddy reaches other containers
- Certificate lifecycle and renewal
- Day-to-day commands (logs, restart, reload)
- Adding a new service
- Troubleshooting

---

## Caddyfile explained

```
(cloudflare_tls) {
    tls {
        dns cloudflare {env.CF_API_TOKEN}
        resolvers 1.1.1.1 8.8.8.8
        propagation_timeout 5m
    }
}
```

`(cloudflare_tls)` is a **snippet** — a reusable block of config that can be imported into any site block. It configures TLS to use the Cloudflare DNS provider. `{env.CF_API_TOKEN}` reads the token from the environment at runtime (set in `.env`, passed through `docker-compose.yml`). `resolvers` tells Caddy which DNS servers to use when verifying that its challenge TXT record has propagated. `propagation_timeout 5m` gives Cloudflare up to 5 minutes to propagate the record before failing — generous but safe on first cert issuance.

```
webui.{$DOMAIN} {
    import cloudflare_tls
    reverse_proxy open-webui:8080
}
```

Each block is a **virtual host**. `{$DOMAIN}` is substituted from the `DOMAIN` environment variable. `import cloudflare_tls` applies the TLS snippet. `reverse_proxy open-webui:8080` forwards all traffic to the `open-webui` container on its internal port `8080`. Caddy automatically handles the HTTP → HTTPS redirect, HSTS headers, and HTTP/3 negotiation.

The four site blocks follow the same pattern, just pointing at different containers:

| Subdomain | Container | Internal port |
|-----------|-----------|---------------|
| `webui.DOMAIN` | `open-webui` | 8080 |
| `docs.DOMAIN` | `docmost` | 3000 |
| `dash.DOMAIN` | `glance` | 8080 |
| `n8n.DOMAIN` | `n8n` | 5678 |

---

## How Caddy reaches other containers

Caddy and all application containers are on the same Docker bridge network: `ai-network`. Docker's internal DNS lets any container reach any other container by its service name. So `reverse_proxy open-webui:8080` works because Docker resolves `open-webui` to the container's private IP on `ai-network`.

No IP addresses are hardcoded anywhere. If a container's IP changes (e.g. after a recreate), Docker updates the DNS entry and Caddy reconnects automatically.

Ollama is the one service **not** on `ai-network` — it runs natively on the host. Caddy does not proxy to Ollama; Open WebUI handles that directly via `host.docker.internal`.

---

## Certificate lifecycle

Caddy manages the full certificate lifecycle automatically:

1. **First start** — Caddy performs the DNS-01 ACME challenge via Cloudflare, receives a certificate from Let's Encrypt, and stores it in the `caddy_data` volume.
2. **Subsequent starts** — Caddy loads the stored certificate from `caddy_data`. No challenge is performed.
3. **Renewal** — Let's Encrypt certificates expire after 90 days. Caddy automatically renews them ~30 days before expiry while running in the background. No intervention needed.
4. **Volume loss** — If the `caddy_data` volume is deleted, Caddy re-issues certificates from scratch on next start.

> Do not delete `caddy_data` unless you want to force re-issuance. Let's Encrypt rate-limits issuance to 5 certificates per domain per week.

To inspect stored certificates:

```bash
docker exec caddy caddy list-certificates
```

---

## Day-to-day commands

### View logs

```bash
# Live log stream
docker compose logs -f caddy

# Recent logs only
docker compose logs --tail 50 caddy
```

Lines to look for:

| Log message | Meaning |
|-------------|---------|
| `certificate obtained successfully` | New cert issued |
| `certificate renewed` | Renewal succeeded |
| `failed to get certificate` | ACME challenge failed |
| `no upstream` | Upstream container is down |

### Restart Caddy

```bash
docker compose restart caddy
```

Caddy reloads its config and reconnects to upstreams. TLS certs are preserved (stored in volume, not lost on restart).

### Reload config without restart

Caddy supports live config reload — no downtime, no dropped connections:

```bash
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Use this after editing the `Caddyfile` when you don't want to interrupt active connections.

### Rebuild the Caddy image

Required when `caddy.Dockerfile` changes or the Cloudflare plugin needs updating:

```bash
docker compose build --no-cache caddy
docker compose up -d caddy
```

---

## Adding a new service

To expose a new container via HTTPS:

**1. Add the container to `ai-network` in `docker-compose.yml`:**

```yaml
my-new-service:
  image: ...
  networks:
    - ai-network
```

**2. Add a DNS A record in Cloudflare:**

| Type | Name | IPv4 | Proxy status |
|------|------|------|--------------|
| A | `myservice` | `192.168.1.83` | DNS only (grey) |

**3. Add a site block to `Caddyfile`:**

```
myservice.{$DOMAIN} {
    import cloudflare_tls
    reverse_proxy my-new-service:<internal-port>
}
```

**4. Reload Caddy:**

```bash
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Caddy will automatically request a certificate for the new subdomain.

---

## Troubleshooting

### DNS challenge timeout

```
failed to get certificate: ACME DNS challenge: timeout
```

Causes and fixes:
- **Wrong token permissions** — token must have "Edit zone DNS" permission for your specific zone
- **Extra whitespace in `.env`** — `CF_API_TOKEN=yourtoken ` (trailing space) will fail; check with `docker compose exec caddy env | grep CF_API_TOKEN`
- **A record is proxied (orange cloud)** — must be grey (DNS only)
- **A record doesn't exist yet** — Caddy validates via DNS, not HTTP; the record must exist before Caddy starts

### Certificate not found / "no certificate" error

The `caddy_data` volume may have been deleted. Restart Caddy — it will re-issue:

```bash
docker compose restart caddy
docker compose logs -f caddy
```

### Upstream connection refused

```
dial tcp: connect: connection refused
```

The target container (`open-webui`, `docmost`, or `glance`) is down. Caddy itself is fine.

```bash
# Check which container is down
docker compose ps

# Restart it
docker compose restart open-webui
```

### Checking certificate expiry

```bash
docker exec caddy caddy list-certificates
```

Caddy auto-renews ~30 days before expiry. If renewal fails (e.g. token expired), you'll see errors in `docker compose logs caddy` — fix the token and restart.

### Testing HTTPS from the server

```bash
curl -v https://webui.yourdomain.com 2>&1 | grep -E "SSL|certificate|subject"
```

---

## Files

| File | Purpose |
|------|---------|
| `Caddyfile` | Caddy configuration — virtual hosts, TLS, upstreams |
| `caddy.Dockerfile` | Custom Caddy image build (adds Cloudflare DNS plugin) |
| `caddy_data` volume | TLS certificates and ACME state |
| `caddy_config` volume | Caddy runtime config cache |

`.env` variables used by Caddy:

| Variable | Example | Purpose |
|----------|---------|---------|
| `DOMAIN` | `yourdomain.com` | Root domain for subdomain generation |
| `CF_API_TOKEN` | `abc123...` | Cloudflare API token for DNS-01 challenge |
