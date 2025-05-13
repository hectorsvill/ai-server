# ai-server config 

### Components:
- AMD GPU
- Ubuntu 
- [AMD Driverse intalled](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/quick-start.html)
- [Docker](http://docker.com/)
- [Ollama](https://ollama.com/): A tool for running llm's localy
- [Open WebUI](https://github.com/open-webui/open-webui): A Web interface for interacting with LLMs served by Ollama.

### ollama Setup
Run Docker command to deploy the Ollama server with ROCm support for AMD GPUs: 
```bash
docker run -d \
  --name ollama \
  --network ai-network \
  -v ollama:/root/.ollama \
  -p 11434:11434 \
  --device=/dev/kfd \
  --device=/dev/dri \
  --group-add video \
  --group-add 992 \
  --restart always \
  ollama/ollama:rocm
```
### Open WebUI Setup
Deploy Open WebUI interface using Docker, connecting it to Ollama server:
```bash
docker run -d \
  --name open-webui \
  --network ai-network \
  -p 3000:8080 \
  -v open-webui:/app/backend/data \
  -e OLLAMA_BASE_URL=http://ollama:11434 \
  --add-host=host.docker.internal:host-gateway \
  --restart always \
  ghcr.io/open-webui/open-webui:main
```
### Pulling models 
Use the following command to pull any models to the Ollama server. Example, to pull the `deepseek-r1` model:
```bash 
docker exec -it ollama ollama pull deepseek-r1
```

