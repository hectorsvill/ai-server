# Operations Guide

Setup, maintenance, backup, and troubleshooting for the AI server stack.

See also: [`SERVICES.md`](SERVICES.md) for service roles and architecture, [`CREDENTIALS.md`](CREDENTIALS.md) for password management, [`https-setup.md`](https-setup.md) for Caddy HTTPS setup.

## Table of Contents
1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Services](#services)
4. [Setup Instructions](#setup-instructions)
5. [Usage](#usage)
6. [Maintenance](#maintenance)
7. [Troubleshooting](#troubleshooting)
8. [Security Considerations](#security-considerations)

## Overview

This Docker Compose configuration sets up a complete AI server stack with AMD GPU acceleration, featuring:
- **Ollama**: Local LLM server running natively on the host (not in Docker) for optimal AMD ROCm GPU performance
- **Open WebUI**: Web interface for interacting with Ollama models
- **Glance**: System monitoring and dashboard service
- **Docmost**: Knowledge management and documentation platform
- **PostgreSQL**: Database backend for Docmost
- **Redis**: Cache service for Docmost performance
- **Caddy**: Reverse proxy providing automatic HTTPS via Let's Encrypt and Cloudflare DNS challenge

## Prerequisites

Before starting, ensure you have:

1. **Docker and Docker Compose installed**
   ```bash
   # Install Docker
   sudo apt update
   sudo apt install docker.io docker-compose-plugin
   
   # Add user to docker group
   sudo usermod -aG docker $USER
   # Log out and back in for group changes to take effect
   ```

2. **AMD GPU with ROCm drivers** (Essential for Ollama GPU acceleration)
   - Follow the [AMD ROCm installation guide](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/quick-start.html)
   - Verify installation: `rocm-smi`
   - Ensure your user is in the `video` and `render` groups:
     ```bash
     sudo usermod -aG video,render $USER
     # Verify group membership
     groups $USER
     ```

3. **Verify AMD GPU device access**
   ```bash
   ls -la /dev/kfd /dev/dri
   # Should show accessible devices
   ```

4. **Sufficient disk space** 
   - At least 20GB for Docker volumes
   - Additional space for AI models (2-8GB per model)

5. **Network ports available**:
   - 11434: Ollama API (host systemd service — LAN access blocked by UFW except from Docker subnet)
   - 3234: Open WebUI (localhost-only; access via https://webui.vailab.us)
   - 11457: Glance dashboard (localhost-only; access via https://dash.vailab.us)
   - 4389: Docmost (localhost-only; access via https://wiki.vailab.us)
   - 80: Caddy HTTP (open — redirects to HTTPS)
   - 443: Caddy HTTPS (open)

## Services

### Caddy (Ports 80 & 443) — Reverse proxy + HTTPS
- **Purpose**: Terminates HTTPS for all services and reverse-proxies to them over the internal Docker network
- **Image**: Custom build via `caddy.Dockerfile` (adds Cloudflare DNS plugin with `xcaddy`)
- **Config**: `Caddyfile` in repo root
- **HTTPS URLs**: `webui.yourdomain.com`, `wiki.yourdomain.com`, `dash.yourdomain.com`
- **TLS**: Automatic certificates from Let's Encrypt via Cloudflare DNS-01 challenge
- **Volumes**: `caddy_data` (certs), `caddy_config` (runtime cache)
- **Build**: `docker compose build caddy` (takes ~2 min first time)
- **Logs**: `docker compose logs -f caddy`
- See [`https-setup.md`](https-setup.md) for full setup instructions

### Ollama (Port 11434) — Native host service
- **Purpose**: Runs large language models locally with AMD GPU acceleration
- **Runs as**: Native systemd service (`ollama.service`), not a Docker container
- **Why native**: Direct AMD ROCm GPU access with no driver version conflicts, no device passthrough complexity, and no container networking overhead
- **GPU Support**: AMD GPU via ROCm drivers installed directly on the host
- **Data**: Stored in `~/.ollama` on the host filesystem
- **API Endpoint**: http://localhost:11434 (host) / http://host.docker.internal:11434 (from containers)
- **Manage**: `sudo systemctl start|stop|restart|status ollama`

### Open WebUI (Port 3234)
- **Purpose**: Modern web interface for chatting with AI models
- **Image**: `ghcr.io/open-webui/open-webui:main`
- **Access**: https://webui.vailab.us (or http://localhost:3234 from the server itself)
- **Integration**: Connects to Ollama via `OLLAMA_BASE_URL`
- **Data**: User settings and chat history in `open_webui_data` volume

### Glance (Port 11457)
- **Purpose**: System monitoring dashboard for Docker containers and resources
- **Image**: `glanceapp/glance`
- **Access**: https://dash.vailab.us (or http://localhost:11457 from the server itself)
- **Configuration**: Reads from `./config` directory
- **Monitoring**: Docker socket access for container monitoring
  
   See `GLANCE_GUIDE.md` for detailed Glance configuration, supported widgets, dynamic links, and troubleshooting tips.

### Docmost (Port 4389)
- **Purpose**: Self-hosted knowledge management and documentation platform
- **Image**: `docmost/docmost:latest`
- **Access**: https://wiki.vailab.us (or http://localhost:4389 from the server itself)
- **Dependencies**: Requires PostgreSQL and Redis
- **Data**: Application data stored in `docmost_data` volume

### PostgreSQL Database
- **Purpose**: Primary database for Docmost application
- **Image**: `postgres:16-alpine`
- **Internal Access**: `docmost_db:5432`
- **Database**: `docmost`
- **Data**: Persistent storage in `postgres_data` volume

### Redis Cache
- **Purpose**: High-performance cache for Docmost
- **Image**: `redis:7.2-alpine`
- **Internal Access**: `redis:6379`
- **Data**: Cache data stored in `redis_data` volume

## Setup Instructions

### 1. Initial Setup

1. **Clone or navigate to the project directory**
   ```bash
   cd /home/hectorsvillai/Desktop/ai-server
   ```

2. **Create required directories**
   ```bash
   mkdir -p config assets
   chmod 755 config assets
   ```

3. **Verify AMD GPU setup**
   ```bash
   # Check ROCm installation
   rocm-smi
   
   # Verify GPU devices are accessible
   ls -la /dev/kfd /dev/dri
   
   # Check group memberships
   groups $USER | grep -E 'video|render'
   ```

### 2. Connect native Ollama to Open WebUI

By default, Ollama only listens on `127.0.0.1` (loopback). Docker containers cannot reach loopback on the host — they need Ollama to listen on `0.0.0.0`. This is done via a systemd override so the original service file is never modified.

```bash
# Create the override directory
sudo mkdir -p /etc/systemd/system/ollama.service.d

# Write the override (makes Ollama listen on all interfaces)
sudo tee /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
EOF

# Apply and restart
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Verify Open WebUI can reach Ollama:

```bash
docker exec open-webui curl -s http://host.docker.internal:11434/api/tags
```

You should see a JSON list of your downloaded models. If you get "Connection refused", check that ollama is running: `sudo systemctl status ollama`.

The override file is stored at `/etc/systemd/system/ollama.service.d/override.conf` — it survives reboots and Ollama package updates.

### 3. Security Configuration

⚠️ **CRITICAL**: Configure environment variables for security:

1. **Set up environment variables**:
   ```bash
   # Copy the example file and edit with your values
   cp .env.example .env

   # Edit the .env file with your actual values
   nano .env
   ```

2. **Generate a secure app secret for Docmost**:
   ```bash
   openssl rand -hex 32
   # Copy the output to APP_SECRET in .env file
   ```

3. **Update configuration in .env file**:
   - Replace `your_generated_app_secret_here` with the generated secret
   - Replace `your_server_ip` with your actual server IP address
   - Replace `your_strong_database_password_here` with a strong password
   - Update the DATABASE_URL with the same password

4. **Verify .env file is gitignored**:
   ```bash
   # The .env file should already be listed in .gitignore
   cat .gitignore | grep .env
   ```

### 4. Start the Services

1. **Start all services**:
   ```bash
   docker compose up -d
   ```

2. **Start specific services**:
   ```bash
   # Start only AI services
   docker compose up -d ollama open-webui
   
   # Start documentation services
   docker compose up -d docmost docmost_db redis
   ```

3. **Check service status**:
   ```bash
   docker compose ps
   docker compose logs -f [service_name]
   ```

## Usage

### Setting Up Ollama Models

Ollama runs natively on the host — use the `ollama` CLI directly (no `docker exec` needed).

1. **Pull popular models optimized for AMD GPU**:
   ```bash
   ollama pull llama3.2          # 7B parameters
   ollama pull deepseek-r1       # Advanced reasoning
   ollama pull mistral:7b        # Fast and efficient
   ollama pull codellama:7b      # Code generation
   ollama pull qwen2.5:7b        # Multilingual support
   ```

2. **Check downloaded models**:
   ```bash
   ollama list
   ```

3. **Test model performance**:
   ```bash
   ollama run llama3.2 "Hello, how are you?"
   ```

4. **Monitor GPU usage during model loading**:
   ```bash
   watch -n 1 rocm-smi
   ```

5. **Remove models to free space**:
   ```bash
   ollama rm model_name
   ```

### Accessing Services

- **Open WebUI**: http://localhost:3234
  - Create an account on first visit
  - Select downloaded models from the dropdown
  - Start chatting with your AMD GPU-accelerated AI models
  - Monitor response times (should be fast with GPU acceleration)

- **Glance Dashboard**: http://localhost:11457
  - Monitor AMD GPU utilization
  - View Docker container status and resource usage
  - Track system performance metrics

- **Docmost**: http://localhost:4389
  - Create an admin account on first visit
  - Set up your knowledge base and documentation
  - Perfect for documenting your AI server setup and model configurations

### Configuration Files

#### Glance Configuration
Create `./config/glance.yml` to customize your dashboard for system monitoring:
```yaml
server:
  host: 0.0.0.0
  port: 8080

pages:
  - name: AI Server Dashboard
    columns:
      - size: small
        widgets:
          - type: monitor
            title: System Resources

      - size: full
        widgets:
          - type: calendar

theme:
  background-color: 210 20 98
  contrast-multiplier: 1.2
  primary-color: 280 83 62
  positive-color: 95 66 51
  negative-color: 0 84 60
```

**Important Glance Configuration Notes**:
- Widget types: Use `monitor`, `calendar` - avoid `docker`, `cpu` (these don't exist)
- Theme colors: Must be in HSL format `"hue saturation lightness"` (0-360 0-100 0-100)
- File ownership: Ensure the config file is owned by your user, not root
- File must not be empty: Check `cat config/glance.yml` shows content

## Maintenance

### Regular Tasks

1. **Update images to latest versions**:
   ```bash
   docker compose pull
   docker compose up -d
   ```

   To update native Ollama:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```

2. **Monitor AMD GPU performance**:
   ```bash
   # Real-time GPU monitoring
   watch -n 1 rocm-smi
   
   # Check GPU memory usage
   rocm-smi --showmemuse
   
   # View GPU temperature and power
   rocm-smi --showtemp --showpower
   ```

3. **Clean up unused Docker resources**:
   ```bash
   # Remove unused containers and images
   docker system prune -f
   
   # Clean up volumes (CAREFUL: This removes unused volumes)
   docker volume prune -f
   ```

4. **Backup critical data**:
   ```bash
   # Create backup directory
   mkdir -p ./backups
   
   # Backup Ollama models (native host path)
   tar czf ./backups/ollama_backup_$(date +%Y%m%d).tar.gz -C ~/.ollama .

   # Backup PostgreSQL database
   docker exec docmost-postgresql pg_dump -U docmost docmost > ./backups/docmost_db_$(date +%Y%m%d).sql
   
   # Backup Docmost application data
   docker run --rm -v docmost_data:/source -v $(pwd)/backups:/backup alpine tar czf /backup/docmost_data_$(date +%Y%m%d).tar.gz -C /source .
   ```

### Monitoring

1. **Check service logs**:
   ```bash
   # Ollama logs (GPU initialization and model loading) — native systemd service
   sudo journalctl -u ollama -f
   
   # Open WebUI logs
   docker compose logs -f open-webui
   
   # Docmost application logs
   docker compose logs -f docmost
   ```

2. **Monitor system and GPU resources**:
   ```bash
   # Container resource usage
   docker stats
   
   # System resources
   htop
   
   # AMD GPU status and utilization
   rocm-smi
   
   # Continuous GPU monitoring
   watch -n 2 rocm-smi
   ```

3. **Check model performance**:
   ```bash
   # Test model response time
   time docker exec -it ollama ollama run llama3.2 "Write a short poem"
   ```

### Startup Verification Checklist

After starting services with `docker compose up -d`, verify everything is working:

```bash
# 1. Check all containers are running
docker compose ps

# 2. Wait for services to initialize (especially database)
sleep 30

# 3. Check logs for any errors
docker compose logs --tail 50

# 4. Verify specific service health
echo "Testing Docmost..."
curl -s -I http://localhost:4389 | head -n1

echo "Testing Open WebUI..."
curl -s -I http://localhost:3234 | head -n1

echo "Testing Ollama API (native host service)..."
curl -s -I http://localhost:11434 | head -n1

echo "Testing Glance Dashboard..."
curl -s -I http://localhost:11457 | head -n1

# 5. Check database connectivity for Docmost
docker compose logs docmost | grep -E "successfully started|Migration.*executed successfully" | tail -5

# 6. Verify AMD GPU access (native Ollama service)
rocm-smi 2>/dev/null || echo "ROCm not accessible"

# 7. List available models
ollama list
```

### Complete Reset Procedure

If you encounter persistent issues, here's how to completely reset:

```bash
# 1. Stop everything
docker compose down

# 2. Remove all volumes (WARNING: This deletes all data!)
docker volume rm ai-server_open_webui_data ai-server_docmost_data ai-server_postgres_data ai-server_redis_data
# Note: Ollama models are in ~/.ollama on the host — remove manually if needed

# 3. Clean up Docker resources
docker system prune -f

# 4. Verify .env configuration
cat .env

# 5. Start fresh
docker compose up -d

# 6. Monitor startup logs
docker compose logs -f
```

## Troubleshooting

### Common Issues

1. **Caddy DNS challenge fails / cert not issued**:
   ```bash
   # Check logs for DNS challenge errors
   docker compose logs caddy | grep -i "dns\|error\|timeout"

   # Verify environment variables are set
   docker compose exec caddy env | grep -E "CF_API_TOKEN|DOMAIN"

   # Common causes:
   # - CF_API_TOKEN missing or has incorrect permissions (needs "Edit zone DNS")
   # - DNS A records not created in Cloudflare, or set to proxied (orange cloud)
   # - CF_API_TOKEN has extra spaces in .env
   ```

2. **Caddy container won't start / build fails**:
   ```bash
   # Rebuild the custom Caddy image
   docker compose build --no-cache caddy

   # Start and watch logs
   docker compose up -d caddy
   docker compose logs -f caddy
   ```

3. **Browser shows "Not secure" / cert warning after Caddy starts**:
   ```bash
   # Cert may still be issuing — wait ~60s and retry
   docker compose logs caddy | grep "certificate obtained"

   # Confirm DNS A records point to 192.168.1.83 and are NOT proxied
   ```

4. **AMD GPU not detected in Ollama**:
   ```bash
   # Verify ROCm installation
   rocm-smi

   # Check if ollama user/service has access to GPU groups
   groups $USER | grep -E 'video|render'

   # Verify device permissions
   ls -la /dev/kfd /dev/dri

   # Add user to groups if missing
   sudo usermod -aG video,render $USER
   # Then logout and login again

   # Check native Ollama service logs for GPU initialization
   sudo journalctl -u ollama -n 50 | grep -i gpu
   ```

2. **Services won't start**:
   ```bash
   # Check port conflicts
   ss -tulpn | grep -E ":11434|:3234|:4389|:11457"
   
   # Check for conflicting processes
   docker ps -a
   
   # Review service logs for specific errors
   docker compose logs [service_name]
   ```

3. **Database connection errors (Docmost)**:
   ```bash
   # Check Docmost logs for specific error messages
   docker compose logs docmost
   
   # Look for password authentication errors (code 28P01)
   docker compose logs docmost | grep -i "password authentication failed"
   
   # Restart database services in order
   docker compose restart docmost_db
   docker compose restart redis
   docker compose restart docmost
   
   # Check database logs
   docker compose logs docmost_db
   ```

4. **Environment variable/password mismatch issues**:
   ```bash
   # If you see "password authentication failed for user 'docmost'" errors:
   
   # 1. Stop all services
   docker compose down
   
   # 2. Remove PostgreSQL data volume to reset database
   docker volume rm ai-server_postgres_data
   
   # 3. Remove Docmost data volume (optional, for complete reset)
   docker volume rm ai-server_docmost_data
   
   # 4. Verify your .env file has correct values
   cat .env
   
   # 5. Start services with fresh database
   docker compose up -d
   
   # 6. Wait for database initialization and check logs
   docker compose logs -f docmost
   ```

5. **Models won't download or run slowly**:
   ```bash
   # Check available disk space
   df -h
   
   # Verify internet connectivity from Ollama container
   docker exec -it ollama curl -I https://ollama.com
   
   # Check GPU memory availability
   rocm-smi --showmemuse
   
   # Test with a smaller model first
   docker exec -it ollama ollama pull tinyllama:1.1b
   ```

6. **Out of GPU memory errors**:
   ```bash
   # Check current GPU memory usage
   rocm-smi --showmemuse
   
   # Stop and remove large models
   docker exec -it ollama ollama rm large_model_name
   
   # Use smaller models or reduce concurrent usage
   docker exec -it ollama ollama pull phi:2.7b  # Smaller alternative
   ```

7. **Service accessibility issues**:
   ```bash
   # Test if services are responding
   curl -I http://localhost:4389  # Docmost
   curl -I http://localhost:3234  # Open WebUI
   curl -I http://localhost:11457 # Glance
   curl -I http://localhost:11434 # Ollama API
   
   # Check if services are listening on ports
   ss -tulpn | grep -E ":11434|:3234|:4389|:11457"
   
   # Verify Docker port mappings
   docker port docmost
   docker port open-webui
   docker port ollama
   docker port glance
   ```

8. **Glance configuration and startup issues**:
   ```bash
   # Check if Glance is restarting repeatedly
   docker compose ps glance
   
   # Check Glance logs for configuration errors
   docker logs glance --tail 20
   
   # Common Glance issues and fixes:
   
   # Issue 1: Empty or missing configuration file
   ls -la config/glance.yml
   cat config/glance.yml  # Should not be empty
   
   # Issue 2: File ownership problems
   sudo chown $USER:$USER config/glance.yml
   
   # Issue 3: Invalid widget types (docker widget doesn't exist)
   # Use valid widgets: monitor, calendar, etc.
   
   # Issue 4: Invalid theme colors (must be HSL format)
   # Colors should be: "hue saturation lightness" (0-360 0-100 0-100)
   
   # Create minimal working Glance configuration:
   cat > config/glance.yml << 'EOF'
server:
  host: 0.0.0.0
  port: 8080

pages:
  - name: AI Server Dashboard
    columns:
      - size: small
        widgets:
          - type: monitor
            title: System Resources
      - size: full
        widgets:
          - type: calendar

theme:
  background-color: 210 20 98
  contrast-multiplier: 1.2
  primary-color: 280 83 62
  positive-color: 95 66 51
  negative-color: 0 84 60
EOF
   
   # Restart Glance after configuration fix
   docker compose restart glance
   
   # Verify Glance is running
   sleep 10 && docker compose ps glance
   ```

### Configuration Issues

1. **Environment variable problems**:
   ```bash
   # Verify environment variables are loaded correctly
   docker compose config
   
   # Check if .env file exists and has correct format
   cat .env
   
   # Ensure no syntax errors in .env file
   # Variables should be: KEY=value (no spaces around =)
   
   # Test environment variable substitution
   docker compose exec docmost env | grep -E "APP_SECRET|DATABASE_URL|POSTGRES"
   ```

2. **Docker Compose configuration validation**:
   ```bash
   # Validate docker-compose.yml syntax
   docker compose config --quiet
   
   # View final configuration with environment variables resolved
   docker compose config
   
   # Check for port conflicts
   docker compose config | grep -A2 -B2 ports:
   ```

3. **Configuration file problems**:
   ```bash
   # Check if required configuration files exist and have content
   ls -la config/
   
   # Verify Glance configuration
   if [ -f config/glance.yml ]; then
     echo "Glance config exists"
     wc -l config/glance.yml  # Should not be 0 lines
   else
     echo "Creating missing Glance configuration..."
     mkdir -p config
     cat > config/glance.yml << 'EOF'
server:
  host: 0.0.0.0
  port: 8080
pages:
  - name: AI Server Dashboard
    columns:
      - size: small
        widgets:
          - type: monitor
            title: System Resources
      - size: full
        widgets:
          - type: calendar
theme:
  background-color: 210 20 98
  contrast-multiplier: 1.2
  primary-color: 280 83 62
  positive-color: 95 66 51
  negative-color: 0 84 60
EOF
   fi
   
   # Fix ownership of configuration files
   sudo chown -R $USER:$USER config/
   chmod -R 644 config/*.yml
   
   # Validate YAML syntax
   python3 -c "import yaml; yaml.safe_load(open('config/glance.yml'))" 2>/dev/null || echo "YAML syntax error in glance.yml"
   ```

### Performance Issues

1. **Slow model responses on AMD GPU**:
   ```bash
   # Verify GPU is being utilized
   rocm-smi -d
   
   # Check for thermal throttling
   rocm-smi --showtemp
   
   # Ensure ROCm drivers are properly loaded
   lsmod | grep amdgpu
   
   # Try smaller models if GPU memory is limited
   docker exec -it ollama ollama pull mistral:7b
   ```

2. **High memory usage**:
   ```bash
   # Limit concurrent model usage in Open WebUI
   # Use only one model at a time
   
   # Monitor GPU memory
   watch -n 1 "rocm-smi --showmemuse"
   
   # Increase system swap if needed
   sudo fallocate -l 8G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   ```

3. **Container startup failures**:
   ```bash
   # Check for insufficient GPU memory
   rocm-smi --showmemuse
   
   # Verify container permissions for GPU access
   docker exec -it ollama ls -la /dev/kfd /dev/dri
   ```

## Security Considerations

### Network Security

All services are accessible **only** via Caddy at `https://*.vailab.us`. Direct LAN access to service ports is blocked through two complementary mechanisms:

1. **Localhost-only port binding** — `open-webui`, `glance`, and `docmost` ports are bound to `127.0.0.1` in `docker-compose.yml`. This prevents Docker from opening them to the network (Docker bypasses UFW, so UFW rules alone are not sufficient).

2. **UFW firewall** — Only the following ports are open:
   - `22195/tcp` — SSH
   - `80/tcp`, `443/tcp` — Caddy HTTP/HTTPS
   - `11434/tcp` from `172.19.0.0/16` — Ollama, reachable only from the Docker subnet

See [`UFW.md`](UFW.md) for the full firewall guide and how to add rules safely.

### Data Security
- **Critical**: Change the PostgreSQL password from `STRONG_DB_PASSWORD` immediately
- Generate a secure Docmost app secret: `openssl rand -hex 32`
- All application data persists in named Docker volumes

### Access Control
- Configure user authentication in Open WebUI during first setup
- Set up proper user permissions in Docmost

### AMD GPU Security
- GPU device access is limited to containers that explicitly need it
- Only the Ollama container has direct GPU access
- GPU groups (video, render) provide necessary but limited permissions

### Production Recommendations
- Use environment files (.env) for sensitive configuration — never commit `CF_API_TOKEN` or `DOMAIN`
- Implement regular automated backups
- Monitor system logs for unusual GPU or container activity
- Keep ROCm drivers and Docker images updated
- Caddy provides HTTPS with browser-trusted Let's Encrypt certificates — see [`https-setup.md`](https-setup.md)

---

**Need help?**
- Check the troubleshooting section above
- Review service logs: `docker compose logs [service]` or `sudo journalctl -u ollama -f`
- Monitor AMD GPU status: `rocm-smi`
- Official docs: [Ollama](https://ollama.com/), [Open WebUI](https://github.com/open-webui/open-webui), [Docmost](https://docmost.com/)