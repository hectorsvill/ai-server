# ai-server docker config 

### Components:
- AMD GPU
- Ubuntu 
- [AMD Driverse installed](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/quick-start.html)
- [Docker](http://docker.com/)
- [Ollama](https://ollama.com/): A tool for running llm's localy.
- [Open WebUI](https://github.com/open-webui/open-webui): A Web interface for interacting with LLMs served by Ollama.

### ollama Setup
Run Docker command to deploy the Ollama server with ROCm support for AMD GPUs: 
```bash
ollama:
    image: 'ollama/ollama:rocm'
    container_name: ollama
    ports:
      - '11434:11434'
    volumes:
      - ollama_data:/root/.ollama
    devices:
      - /dev/kfd
      - /dev/dri
    group_add:
      - video
      - '992' # Verify this group ID for your system's 'render' or 'video' group.
    networks:
      - ai-network
    restart: always
```
### Open WebUI Setup
Deploy Open WebUI interface using Docker, connecting it to Ollama server:
```bash
open-webui:
    image: 'ghcr.io/open-webui/open-webui:main'
    container_name: open-webui
    ports:
      - '3234:8080' # Host port 3000 for Open WebUI
    volumes:
      - open_webui_data:/app/backend/data
    environment:
      OLLAMA_BASE_URL: http://ollama:11434
    extra_hosts:
      - "host.docker.internal:host-gateway"
    networks:
      - ai-network
    depends_on:
      - ollama
    restart: always
```
### Pulling models 
Use the following command to pull any models to the Ollama server. Example, to pull the `deepseek-r1` model:
```bash 
docker exec -it ollama ollama pull deepseek-r1
```
### docmost
Update Docmost service configuration with new environment variables, port mappings, and dependency updates. Uses volumes for data storage and a consistent network configuration.
```
docmost:
    image: docmost/docmost:latest
    container_name: docmost
    depends_on:
      - docmost_db # Depends on the new PostgreSQL database
      - redis
    environment:
      APP_URL: "http://192.168.1.159:3001" # Updated to new host port
      APP_SECRET: "ac47bb927b965eeb8af2ac7575cb60e58345469e50dbf05d0de714b5f34da658" # Replace with: openssl rand -hex 32 !!!
      DATABASE_URL: "postgresql://docmost:STRONG_DB_PASSWORD@docmost_db:5432/docmost?schema=public" # Updated DB service name
      REDIS_URL: "redis://redis:6379"
    ports:
      - "4389:3000" # Host port 3001 maps to container port 3000
    restart: unless-stopped
    volumes:
      - docmost_data:/app/data/storage # Using a named volume for Docmost data
    networks:
      - ai-network # Connect to the shared network
```

