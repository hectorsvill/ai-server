# Tailscale Remote Access

Tailscale creates a private WireGuard-based overlay network (a "tailnet") between your devices. Once connected, your MacBook or iPhone can reach the AI server as if it were on the same LAN — from anywhere in the world.

---

## How it fits into the stack

```
iPhone / MacBook (anywhere)
        │
        │  Tailscale encrypted tunnel (WireGuard)
        ▼
 Tailscale IP of this server  ← ${TAILSCALE_IP} in .env
        │
        │  port 443 (already open via UFW)
        ▼
    Caddy (0.0.0.0:443)
        ├── webui.DOMAIN  → open-webui:8080
        ├── wiki.DOMAIN   → docmost:3000
        └── dash.DOMAIN   → glance:8080
```

**No Caddyfile changes.** Caddy already binds to `0.0.0.0:443`, so it accepts connections arriving over the Tailscale interface the same way it accepts LAN connections.

**No UFW changes.** Tailscale runs as a kernel module and handles its own encrypted overlay. Traffic arrives on the already-open port 443 — no additional firewall rules are needed.

**TLS still works.** Certificates are issued via Cloudflare DNS-01 challenge (no inbound port needed). The cert doesn't care whether the client connected over LAN or Tailscale.

---

## Setup

### 1. Install Tailscale on the server

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Authenticate in the browser when prompted. The server will appear in your tailnet.

Enable auto-start on boot (usually done automatically by the installer):

```bash
sudo systemctl enable --now tailscaled
```

### 2. Install Tailscale on client devices

- **macOS** — download from tailscale.com or `brew install --cask tailscale`
- **iOS / iPadOS** — App Store → Tailscale
- **Other Linux** — same `curl` installer above

Sign in with the same account on every device. They all join the same tailnet automatically.

### 3. Find the server's Tailscale IP

```bash
tailscale ip -4
```

Save this value as `TAILSCALE_IP` in your `.env` file. It is stable — it doesn't change unless you remove and re-add the device from your tailnet admin panel.

### 4. Update Cloudflare DNS A records

In **Cloudflare → your domain → DNS → Records**, change the A records for all three subdomains from the LAN IP to `${TAILSCALE_IP}`:

| Name | Type | Value |
|------|------|-------|
| `webui` | A | `${TAILSCALE_IP}` |
| `wiki`  | A | `${TAILSCALE_IP}` |
| `dash`  | A | `${TAILSCALE_IP}` |

Keep each record set to **"DNS only"** (grey cloud). DNS proxying (orange cloud) would break the DNS-01 certificate challenge.

DNS propagation is typically under 60 seconds for unproxied records.

### 5. Verify

On any device connected to Tailscale:

```bash
# Should resolve to the Tailscale IP
dig +short webui.yourdomain.com

# Should return HTTP 200 or 301
curl -I https://webui.yourdomain.com
```

Or just open `https://webui.yourdomain.com` in a browser — it should load with a valid certificate.

---

## Day-to-day usage

- **Connect** — Open the Tailscale app on your device and toggle it on.
- **Disconnect** — Toggle off. Services become unreachable from that device until reconnected.
- **The server** — `tailscaled` is enabled as a systemd service and starts automatically on boot. No manual intervention needed.

```bash
# Check Tailscale status on the server
tailscale status

# Check which IP is active
tailscale ip -4
```

---

## Devices on the tailnet

Device names and IPs are personal identifiers — store them in `.env` (`TAILSCALE_IP`) rather than committing them to the repo. The tailnet admin panel at `login.tailscale.com` is the authoritative source for your device list and IPs.

---

## Trade-offs

| | LAN IP in DNS | Tailscale IP in DNS |
|---|---|---|
| Works on home LAN without Tailscale | Yes | No — Tailscale must be running |
| Works away from home | No | Yes |
| Works on home LAN with Tailscale running | Yes | Yes (Tailscale uses a direct connection) |
| Performance on home LAN | LAN speed | Same — Tailscale detects direct path |

With Tailscale IP in DNS: all access (local and remote) goes through Tailscale. Since `tailscaled` starts on boot and the server is always on, this is reliable in practice. If Tailscale ever goes down on the server, services become unreachable even from LAN.

---

## Troubleshooting

**Services unreachable after DNS change:**
- Confirm your device is connected to Tailscale (`tailscale status`)
- Confirm DNS propagated: `dig +short webui.yourdomain.com` should return the Tailscale IP
- Confirm `tailscaled` is running on the server: `systemctl status tailscaled`

**Certificate errors after DNS change:**
- Wait ~60 seconds after the DNS change — Caddy may need to renew
- Check Caddy logs: `docker compose logs caddy | grep -i cert`

**Tailscale IP changed (rare):**
- Update `TAILSCALE_IP` in `.env`
- Update the three A records in Cloudflare to the new IP

---

See [`UFW.md`](UFW.md) for firewall context. See [`caddy.md`](caddy.md) for TLS certificate details.
