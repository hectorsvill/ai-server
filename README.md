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
```yaml
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
```yaml
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
```yaml 
docker exec -it ollama ollama pull deepseek-r1
```
### docmost
Update Docmost service configuration with new environment variables, port mappings, and dependency updates. Uses volumes for data storage and a consistent network configuration.
```yaml
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

  # PostgreSQL database service for Docmost (renamed from 'db' to 'docmost_db')
  docmost_db:
    image: postgres:16-alpine
    container_name: docmost-postgresql
    environment:
      POSTGRES_DB: docmost
      POSTGRES_USER: docmost
      POSTGRES_PASSWORD: STRONG_DB_PASSWORD # !!! IMPORTANT: Set a strong password !!!
    restart: unless-stopped
    volumes:
      - postgres_data:/var/lib/postgresql/data # Using a named volume for PostgreSQL data
    networks:
      - ai-network # Connect to the shared network

  # Redis service for Docmost
  redis:
    image: redis:7.2-alpine
    container_name: docmost-redis
    restart: unless-stopped
    volumes:
      - redis_data:/data # Using a named volume for Redis data
    networks:
      - ai-network # Connect to the shared network
```
#### 
# Nginx Proxy Manager service
The Nginx Proxy Manager (NPM) service acts as a proxy for handling HTTP traffic, while the MariaDB database service provides secure MySQL storage with credentials configured internally. 
```yaml
  app:
    image: 'jc21/nginx-proxy-manager:latest'
    container_name: nginx-proxy-manager
    ports:
      - '80:80'   # HTTP
      - '81:81'   # NPM admin interface
      - '123:443' # HTTPS
    environment:
      DB_MYSQL_HOST: "db" # Refers to the 'db' (MariaDB) service
      DB_MYSQL_PORT: 3306
      DB_MYSQL_USER: "npm"
      DB_MYSQL_PASSWORD: "npm"
      DB_MYSQL_NAME: "npm"
    volumes:
      - ./data:/data
      - ./letsencrypt:/etc/letsencrypt
    networks:
      - ai-network
    depends_on:
      - db # Depends on the MariaDB database
    restart: always

  # MariaDB database service for Nginx Proxy Manager (existing 'db')
  db:
    image: 'jc21/mariadb-aria:latest'
    container_name: npm-mariadb
    environment:
      MYSQL_ROOT_PASSWORD: 'npm'
      MYSQL_DATABASE: 'npm'
      MYSQL_USER: 'npm'
      MYSQL_PASSWORD: 'npm'
    volumes:
      - ./mysql:/var/lib/mysql
    networks:
      - ai-network
    restart: always

    ```

