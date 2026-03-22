# n8n — Workflow Automation

n8n is the workflow automation layer of this stack. It connects the local AI services (Ollama, Open WebUI) to each other and to external tools via a visual node editor.

---

## Access

| Method | URL |
|--------|-----|
| HTTPS (LAN / Tailscale) | `https://n8n.yourdomain.com` |
| Direct (localhost only) | `http://localhost:5679` |

The direct port is bound to `127.0.0.1` — it is not reachable from the LAN. All external access goes through Caddy.

---

## First-time setup

1. Start the stack: `docker compose up -d`
2. Open `https://n8n.yourdomain.com` in a browser.
3. Create an owner account when prompted.
4. The UI is now ready. Workflows and credentials are stored in the `n8n_data` Docker volume.

---

## Key environment variables

| Variable | Purpose |
|----------|---------|
| `N8N_ENCRYPTION_KEY` | Encrypts saved credentials at rest. Generate with `openssl rand -hex 32`. **Do not change after first run** — it will break all saved credentials. |
| `WEBHOOK_URL` | Base URL for webhook trigger nodes. Must be the public HTTPS URL (`https://n8n.yourdomain.com/`) so generated webhook URLs are correct. |
| `N8N_HOST` | Hostname portion of the public URL (`n8n.yourdomain.com`). |
| `N8N_PROTOCOL` | `https` — required for correct webhook URL generation behind Caddy. |
| `GENERIC_TIMEZONE` | `America/Chicago` — controls schedule trigger execution times. |

---

## Integrations with this stack

### Ollama (local LLM API)

n8n can call Ollama directly using the **HTTP Request** node or the built-in **Ollama** node (available in recent n8n versions).

**Connection details from inside n8n's container:**

| Setting | Value |
|---------|-------|
| Base URL | `http://host.docker.internal:11434` |
| No auth required | (Ollama has no auth by default) |

**Using the HTTP Request node:**
- Method: `POST`
- URL: `http://host.docker.internal:11434/api/generate`
- Body (JSON):
  ```json
  {
    "model": "llama3.2",
    "prompt": "{{ $json.input }}",
    "stream": false
  }
  ```
- The response field `response` contains the generated text.

**Using the Ollama node (n8n ≥ 1.22):**
- Add a credential of type **Ollama API**
- Base URL: `http://host.docker.internal:11434`
- No API key needed

**Chat completions (OpenAI-compatible endpoint):**
```
POST http://host.docker.internal:11434/v1/chat/completions
```
This endpoint is OpenAI-compatible — you can use the **OpenAI** node in n8n by pointing it at Ollama's URL and using a dummy API key (`ollama`).

---

### Open WebUI API

Open WebUI exposes an OpenAI-compatible API. Use this if you want workflows that go through Open WebUI's session context, model management, or RAG pipeline rather than raw Ollama.

| Setting | Value |
|---------|-------|
| Base URL | `http://open-webui:8080/api` |
| Auth | Bearer token — generate one in Open WebUI under **Settings → Account → API Keys** |

**Example: send a chat message via HTTP Request node**
- Method: `POST`
- URL: `http://open-webui:8080/api/chat/completions`
- Headers: `Authorization: Bearer <your-api-key>`
- Body (JSON):
  ```json
  {
    "model": "llama3.2",
    "messages": [{ "role": "user", "content": "{{ $json.prompt }}" }]
  }
  ```

Both `open-webui` and `n8n` are on `ai-network`, so no host ports are involved.

---

### Docmost

Docmost has a REST API for managing pages, workspaces, and comments.

| Setting | Value |
|---------|-------|
| Base URL | `http://docmost:3000/api` |
| Auth | API key — generate in Docmost under Settings |

**Example use cases:**
- Trigger a workflow when a document is created (Docmost webhook → n8n webhook trigger)
- Append AI-generated summaries to a Docmost page
- Create a new page from a workflow result

Docmost and n8n are both on `ai-network`, so use the container name `docmost` as the hostname.

---

### Webhooks

n8n generates webhook URLs automatically for **Webhook** trigger nodes. Because `WEBHOOK_URL` is set to `https://n8n.yourdomain.com/`, all generated URLs look like:

```
https://n8n.yourdomain.com/webhook/<uuid>
```

These are reachable from:
- LAN devices (Caddy is bound to `0.0.0.0:443`)
- Tailscale devices
- External services if the Cloudflare DNS A record points to a public IP (not the case by default — see [TAILSCALE.md](TAILSCALE.md))

For **internal-only triggers** (e.g. Docmost calling n8n), use the container-internal URL directly: `http://n8n:5678/webhook/<uuid>`. This avoids leaving the Docker network.

---

## Common workflow patterns

### AI summarization pipeline
```
Webhook trigger
  → HTTP Request (POST /api/generate to Ollama)
  → Respond to Webhook (return summary)
```

### Scheduled model pull / update
```
Schedule trigger (e.g. weekly)
  → Execute Command node: ollama pull <model>
```
Not possible directly from n8n (n8n cannot SSH to the host). Use a **webhook → host-side script** pattern instead, or trigger via `curl` from a cron job.

### Document-to-AI pipeline
```
Docmost webhook (page created)
  → HTTP Request (fetch page content via Docmost API)
  → Ollama node (summarize / classify)
  → HTTP Request (write result back to Docmost)
```

### Alert on GPU overload
```
Schedule trigger (every 5 min)
  → HTTP Request: GET http://host.docker.internal:40404/  (rocm-stats)
  → If node: GPU temp > threshold
  → Send notification (email / Slack / Telegram)
```

---

## Data and persistence

n8n stores everything in a single SQLite database inside the `n8n_data` Docker volume:
- Workflow definitions
- Encrypted credentials
- Execution history

No external database is required. The volume is backed up by `tools/backup.sh` — see [BACKUP.md](BACKUP.md).

**Encryption key warning:** The `N8N_ENCRYPTION_KEY` must remain constant. If it changes, n8n cannot decrypt saved credentials and they must be re-entered.

---

## Logs and troubleshooting

```bash
# Live logs
docker compose logs -f n8n

# Restart n8n
docker compose restart n8n

# Access n8n CLI (inside container)
docker compose exec n8n n8n --help
```

**Webhook URLs not working:**
- Confirm `WEBHOOK_URL` in `.env` matches your public domain exactly (trailing slash required).
- Confirm the DNS A record for `n8n.yourdomain.com` resolves to the server IP.
- Check Caddy logs: `docker compose logs -f caddy`.

**Credentials show as invalid after restart:**
- The `N8N_ENCRYPTION_KEY` changed — restore the original key from `~/.credentials/ai-server.txt`.

**Cannot reach Ollama from n8n:**
- Verify `host.docker.internal` resolves: `docker compose exec n8n curl http://host.docker.internal:11434/api/tags`
- Confirm the UFW rule allows the Docker subnet: `sudo ufw status`

---

## Adding n8n to Glance dashboard

Add a bookmark or monitor widget in `config/glance.yml` to quick-link to the n8n editor. See [GLANCE_GUIDE.md](GLANCE_GUIDE.md).

---

See [SERVICES.md](SERVICES.md) for architecture overview, [BACKUP.md](BACKUP.md) for backup procedures, and [caddy.md](caddy.md) for reverse proxy configuration.
