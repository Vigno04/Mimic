# Mimic

Early stages of a platform to create discord bots powered by LLM that following a predefined personality will act as close as possible to real member of the community.

---

## 🚀 Quick Start with Docker

### Option 1: Run with Docker Compose (Local Build)

1. Ensure **Docker** and **Docker Compose** are installed and running.
2. Clone the repository:
   ```bash
   git clone https://github.com/Vigno04/Mimic.git
   cd Mimic
   ```
3. Copy environment settings (optional):
   ```bash
   cp .env.example .env
   ```
4. Start the application:
   ```bash
   docker compose up -d --build
   ```
5. Open the web dashboard in your browser:
   **[http://localhost:8000](http://localhost:8000)**

---

### Option 2: Pull Pre-built Image from GitHub Container Registry (GHCR)

Pre-built Docker images are automatically published to **GitHub Container Registry (`ghcr.io`)**:

- **Latest Stable Release (from `main` branch):**
  ```bash
  docker pull ghcr.io/vigno04/mimic:latest
  ```

- **Development Build (from `develop` branch):**
  ```bash
  docker pull ghcr.io/vigno04/mimic:develop
  ```

Run directly with Docker:
```bash
docker run -d \
  --name mimic \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  ghcr.io/vigno04/mimic:latest
```

---