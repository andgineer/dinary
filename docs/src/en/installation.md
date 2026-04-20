# Installation

## Quick start (Docker, local development)

The simplest way to run dinary-server locally is with Docker:

```bash
git clone https://github.com/andgineer/dinary-server.git
cd dinary-server

# Place your Google service account key where docker-compose.yml expects it
mkdir -p ~/.config/gspread
cp /path/to/your-key.json ~/.config/gspread/service_account.json

cp .env.example .env
# Edit .env if needed (sheet logging, credentials path, etc.)
docker compose up -d
```

!!! tip
    Don't have a service account key yet? See [Google Sheets Setup](google-sheets-setup.md) first.

## Production deployment

- [Oracle Cloud Free Tier](deploy-oracle.md) — $0/month, always-on VM
- [Your own computer](deploy-selfhost.md) — $0, Tailscale Funnel or Cloudflare Tunnel

## Local development (without Docker)

See the [README](https://github.com/andgineer/dinary-server#local-development) for local development setup with `uv`.
