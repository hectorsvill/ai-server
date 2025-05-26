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

