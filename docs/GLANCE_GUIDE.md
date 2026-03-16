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
- Only a subset of widgets are available in the image used here. We validated the following as working and reliable:
  - `monitor` (system resource overview)
  - `clock` (time display)
  - `weather` (basic weather)
  - `calendar`
  - `bookmarks` (links in list or grid)
  - `rss` (news feeds)

> Note: Some widgets found in online examples (e.g., `docker`, `cpu`, `memory`, `storage`, `processes`) were not recognized by this image and caused restarts. We avoided these.

## Current configuration (summary)

Located at `config/glance.yml`:

- Page: "AI Server Dashboard"
- Columns:
  1) Small column: `monitor`, `clock`, `weather`
  2) Small column: Two `bookmarks` groups (AI services and system monitoring)
  3) Full column: `rss` (AI/tech news) and a `bookmarks` grid for quick actions
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

- title: "📖 Docmost Wiki"
  url: "https://wiki.yourdomain.com"
```

See [`https-setup.md`](https-setup.md) for Caddy setup.

## System resources monitoring

- The `monitor` widget shows a concise overview (CPU, memory, disk, network) as provided by this Glance image. No extra config is required.
- Since other resource widgets were not supported by this build, we keep the configuration simple and stable with one well-working monitor widget.

Tips:
- Keep `cache: 5s` for a responsive yet light refresh cadence.
- Avoid adding unrecognized widget types as they will make Glance restart in a loop.

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

## Quick reference: Supported widgets (validated here)

- monitor
- clock
- weather
- calendar
- bookmarks (style: list or grid)
- rss

If you try a widget and Glance begins restarting, remove it and check logs for the exact error.

---

Glance gives you a simple, low-overhead status page and launchpad for your AI server. The configuration in this repo is stable on the current image and easy to extend as your stack evolves.
