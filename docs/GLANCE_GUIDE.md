# Glance Dashboard Guide

This guide explains how the Glance dashboard is set up in this stack, what widgets are used, how they work, and how to customize them safely.

## What is Glance?

Glance is a lightweight dashboard you host yourself. It renders a configurable homepage with useful widgets like system monitor, calendar, weather, bookmarks, and RSS feeds. In this stack, Glance provides at-a-glance visibility and quick links for your AI server.

- Container image: `glanceapp/glance`
- Port: 11457 (mapped to container 8080)
- Config file: `config/glance.yml` (mounted to `/app/config/glance.yml`)

## How it works in this project

- The Docker Compose service `glance` mounts two folders:
  - `./config` → `/app/config` (for `glance.yml`)
  - `./assets` → `/app/assets` (for optional images/fonts)
- The container reads `config/glance.yml` on startup and whenever it changes (it auto-reloads on change).
- The image used here is **v0.8.4**. Confirmed working widgets:
  - `clock` (time display)
  - `server-stats` (CPU, memory, disk — mounts `/:/host:ro` for host filesystem)
  - `monitor` (URL health checks with icons)
  - `docker-containers` (running/stopped container list — mounts docker.sock)
  - `extension` (custom HTML widget from a local HTTP endpoint)
  - `weather`, `calendar`, `bookmarks`, `rss`

> Note: Widgets like `docker`, `cpu`, `memory`, `storage`, `processes` were not recognized by older images and caused restart loops. On v0.8.4 the above list is confirmed safe. If you try an unlisted widget and Glance begins restarting, remove it and check `docker logs glance`.

## Current configuration (summary)

Located at `config/glance.yml`:

- Page: "Home"
- Columns:
  1) Left (small): `clock`, `server-stats` (host CPU/RAM/disk), `extension` (RX 7900 GRE GPU stats)
  2) Center (small): `monitor` (service health checks), `docker-containers`
  3) Right (full): `search`, `rss` (AI/tech news)
- Theme: HSL color values (hue saturation lightness)

## Dynamic host links (why & how)

You’ll often want to link to services running on the same host but different ports (Open WebUI, Docmost, Ollama). Hardcoding an IP in YAML makes it brittle. Instead, this dashboard uses small JavaScript URLs that construct links based on the current hostname.

Example (from `config/glance.yml`):

```yaml
- title: "💬 Open WebUI (AI Chat)"
  url: "javascript:location.protocol + ‘//’ + location.hostname + ‘:3234’"
```

This makes the link work regardless of whether you access the dashboard via IP or a domain name.

If you have Caddy + HTTPS set up, you can hardcode the HTTPS subdomains instead for cleaner URLs:

```yaml
- title: "💬 Open WebUI (AI Chat)"
  url: "https://webui.yourdomain.com"

- title: "📖 Docmost Docs"
  url: "https://docs.yourdomain.com"
```

See [`https-setup.md`](https-setup.md) for Caddy setup.

## System resources monitoring

- `server-stats` — shows CPU, memory, and disk. Uses `/:/host:ro` volume mount so it can read host filesystem metrics.
- `monitor` — health-checks a list of URLs (services). Reports up/down with icons.
- `docker-containers` — lists all Docker containers and their state. Requires the `/var/run/docker.sock:/var/run/docker.sock:ro` mount (already present).

## GPU stats widget (RX 7900 GRE)

The `extension` widget hits a local HTTP endpoint running on the host and embeds the HTML response.

**Endpoint:** `tools/rocm-stats.py` — a small Python HTTP server (systemd service `rocm-stats`) on port 40404.
Runs `rocm-smi` and returns GPU[0] (7900 GRE) metrics: utilization, VRAM %, power draw vs cap, edge/junction temps, shader clock.

**Key requirement:** The server must respond with a `Widget-Content-Type: html` header, otherwise Glance renders the body as plain text.

```yaml
- type: extension
  title: RX 7900 GRE
  url: http://host.docker.internal:40404/
  cache: 5s
  allow-potentially-dangerous-html: true  # needed for inline styles on progress bars
```

The glance service needs `extra_hosts: ["host.docker.internal:host-gateway"]` in `docker-compose.yml` to resolve the host address (already configured).

**Manage the service:**
```bash
sudo systemctl status rocm-stats
sudo systemctl restart rocm-stats
journalctl -u rocm-stats -f

# Smoke-test the endpoint
curl -sv http://localhost:40404/ 2>&1 | grep -i "widget-content"
```

**UFW:** Port 40404 is allowed only from the Docker subnet (`172.19.0.0/16`). See `docs/UFW.md`.

## RSS feeds

The `rss` widget aggregates AI/tech news. If a feed goes offline (404), Glance will keep running and log a warning. You can replace feeds with any valid RSS URL.

To change feeds:
```yaml
- type: rss
  title: 🗞️ AI & Technology News
  limit: 10
  feeds:
    - url: https://example.com/feed.xml
      title: Example Feed
```

## Theme notes (HSL required)

Glance expects theme colors as HSL values (hue 0–360, saturation 0–100, lightness 0–100). Example:

```yaml
primary-color: 280 83 62
```

Using RGB-style triples (e.g., `240 244 247`) causes errors like: "HSL saturation must be between 0 and 100".

## Common pitfalls and fixes

- Container restarts repeatedly:
  - Check logs: `docker logs glance --tail 50`
  - Causes include: bad widget type, bad theme color format, missing/empty config file
- Unknown widget type:
  - Remove or replace with supported widgets (monitor, clock, weather, calendar, bookmarks, rss)
- Config file empty or wrong ownership:
  - Ensure `config/glance.yml` is not empty and owned by your user
  - `sudo chown $USER:$USER config/glance.yml`
- HSL color errors:
  - Use `hue saturation lightness` values, not RGB
- RSS 404:
  - Replace the dead feed URL; Glance will continue to run regardless

## How to edit and apply changes

1. Edit `config/glance.yml`
2. Save the file
3. Glance auto-reloads. If not, restart:

```bash
# Restart Glance and verify
docker compose restart glance
sleep 5
docker compose ps glance
docker logs glance --tail 50
```

## Extending the dashboard

- Add more `bookmarks` groups for your tools or docs
- Add project-specific RSS feeds
- Include additional pages in `pages:` with different widgets/layouts
- If you later switch to a Glance build that supports more widgets, you can try adding them incrementally and watch logs for errors

## Quick reference: Supported widgets (validated on v0.8.4)

| Widget | Purpose |
|--------|---------|
| `clock` | Current time |
| `server-stats` | Host CPU, memory, disk (needs `/:/host:ro`) |
| `monitor` | URL health checks |
| `docker-containers` | Container state list (needs docker.sock) |
| `extension` | Custom HTML from local HTTP endpoint |
| `weather` | Weather forecast |
| `calendar` | Calendar events |
| `bookmarks` | Link lists (style: list or grid) |
| `rss` | RSS/Atom feeds |

If you try a widget and Glance begins restarting, remove it and check logs: `docker logs glance --tail 50`.

---

Glance gives you a simple, low-overhead status page and launchpad for your AI server. The configuration in this repo is stable on the current image and easy to extend as your stack evolves.
