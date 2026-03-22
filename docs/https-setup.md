# HTTPS Setup — Caddy + Cloudflare DNS

Adds HTTPS to all services using Caddy as a reverse proxy and Cloudflare for TLS certificate issuance via DNS challenge. No ports need to be open to the internet.

**What you'll end up with:**
- `https://webui.yourdomain.com` — Open WebUI
- `https://docs.yourdomain.com` — Docmost
- `https://dash.yourdomain.com` — Glance

---

## How It Works (Quick Concept)

```bash
Your browser
    │
    │ https://webui.yourdomain.com
    ▼
Caddy (port 443) ◄── TLS cert from Let's Encrypt (via Cloudflare DNS challenge)
    │
    │ http (internal Docker network)
    ▼
open-webui:8080
```

- **Caddy** sits in front of all services and handles HTTPS termination
- **Let's Encrypt** issues a free, real, browser-trusted TLS certificate
- **Cloudflare DNS challenge** proves you own the domain without exposing port 80/443 to the internet
- Your A records point to `192.168.1.83` (your private LAN IP) — the domain is public but unreachable from outside

---

## Step 1 — Buy a Domain via Cloudflare Registrar

> Recommended: buy directly through Cloudflare Registrar — at-cost pricing (no markup, ever), WHOIS privacy free, and DNS is configured automatically since everything is in one place.

1. Go to [cloudflare.com](https://cloudflare.com) → **Sign Up** (free account)
2. In the left sidebar → **Domain Registration** → **Register a Domain**
3. Search for your domain name → purchase it
4. Confirm your email

> **Watch out for dark patterns on other registrars:** pre-checked add-ons (privacy protection, SSL, email hosting — you don't need any of these), auto-enrolled renewals buried in fine print, and upsell popups. Cloudflare Registrar has none of this.

> DNS and nameservers are configured automatically — no extra setup needed. Skip straight to Step 2.

---

## Step 2 — Create DNS Records in Cloudflare

Once your domain is active in Cloudflare:

1. Go to your domain → **DNS** → **Records** → **Add record**
2. Add the following 3 records (one at a time):

| Type | Name | IPv4 address | Proxy status |
|------|------|--------------|--------------|
| A | `webui` | `192.168.1.83` | DNS only (grey cloud) |
| A | `docs` | `192.168.1.83` | DNS only (grey cloud) |
| A | `n8n`  | `192.168.1.83` | DNS only (grey cloud) |
| A | `dash` | `192.168.1.83` | DNS only (grey cloud) |

> **Critical:** Click the orange cloud to turn it grey (DNS only). Proxied mode won't work with private IPs.

> These records are publicly visible but `192.168.1.83` is a private IP — nobody on the internet can reach it.

---

## Step 3 — Create a Cloudflare API Token

Caddy needs this token to create DNS TXT records for the Let's Encrypt challenge.

1. Cloudflare → top-right avatar → **My Profile** → **API Tokens**
2. Click **Create Token**
3. Use the **"Edit zone DNS"** template
4. Under **Zone Resources**: Include → Specific zone → select your domain
5. Click **Continue to summary** → **Create Token**
6. **Copy the token** — it is only shown once

> Save it temporarily (e.g. a notes app). You'll paste it into `.env` in Step 5.

---

## Step 4 — Open Firewall Ports

Run this on your server:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload
sudo ufw status
```

> Port 80 is needed for HTTP→HTTPS redirect. Port 443 is HTTPS. These only need to be open on your LAN (UFW blocks external access unless you forward them at the router, which you haven't).

---

## Step 5 — Update .env

Add these lines to your `.env` file (replace placeholders with real values):

```bash
# Caddy + HTTPS
DOMAIN=yourdomain.com
CF_API_TOKEN=your_cloudflare_api_token_here

# Update Docmost URL (change from http to https)
APP_URL=https://docs.yourdomain.com
```

---

## Step 6 — Build and Start Caddy

```bash
cd ~/Desktop/ai-server

# Build Caddy with Cloudflare DNS plugin (takes ~2 min first time)
docker compose build caddy

# Start Caddy
docker compose up -d caddy

# Watch logs — look for "certificate obtained successfully"
docker compose logs -f caddy
```

You should see lines like:
```
caddy  | {"level":"info","msg":"certificate obtained successfully","identifier":"webui.yourdomain.com"}
caddy  | {"level":"info","msg":"certificate obtained successfully","identifier":"docs.yourdomain.com"}
caddy  | {"level":"info","msg":"certificate obtained successfully","identifier":"n8n.yourdomain.com"}
caddy  | {"level":"info","msg":"certificate obtained successfully","identifier":"dash.yourdomain.com"}
```

Press `Ctrl+C` to exit log follow.

---

## Step 7 — Update Glance Dashboard URLs

Edit `config/glance.yml` and update the monitor URLs to use HTTPS (replace `yourdomain.com`):

```yaml
- title: Open WebUI
  url: https://webui.yourdomain.com

- title: Docmost
  url: https://docs.yourdomain.com

- title: Glance Dashboard
  url: https://dash.yourdomain.com
```

Then restart Glance:
```bash
docker compose restart glance
```

---

## Step 8 — Verify

Open each URL in your browser — you should see a green padlock in the address bar:

- `https://webui.yourdomain.com` → Open WebUI
- `https://docs.yourdomain.com` → Docmost
- `https://n8n.yourdomain.com` → n8n
- `https://dash.yourdomain.com` → Glance

> Old direct-port URLs (`http://192.168.1.83:3234` etc.) still work as fallback until you decide to remove them.

---

## Troubleshooting

**`docker compose logs caddy` shows DNS challenge timeout**
- Check the Cloudflare API token has "Edit zone DNS" permission
- Check `CF_API_TOKEN` in `.env` has no extra spaces
- Make sure the A records exist in Cloudflare and are set to DNS only (grey)

**Browser shows "Not secure" / cert warning**
- Cert may still be issuing — wait 60 seconds and retry
- Check logs for errors

**Site doesn't load at all**
- Confirm the A record matches `192.168.1.83`
- Try `curl -v https://webui.yourdomain.com` from the server itself

---

## Optional — Remove Direct Port Access

Once HTTPS is confirmed working, you can remove the individual port mappings from `docker-compose.yml` for open-webui, docmost, and glance so those services are only accessible via Caddy:

```yaml
# Remove these port lines from each service:
ports:
  - "3234:8080"   # open-webui
  - "4389:3000"   # docmost
  - "11457:8080"  # glance
```

Then:
```bash
docker compose up -d
```

---

## Future: Adding Pi-hole for Offline DNS

When internet is unavailable, your devices can't resolve `yourdomain.com → 192.168.1.83` because DNS lookups go to Cloudflare's public servers. Pi-hole can be added later to handle DNS locally so everything works offline. See `docs/pihole-setup.md` (future doc).
