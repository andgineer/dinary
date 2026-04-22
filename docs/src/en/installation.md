# Installation

## Quick start (Docker, local development)

The simplest way to run dinary locally is with Docker:

```bash
git clone https://github.com/andgineer/dinary.git
cd dinary

# Place your Google service account key where docker-compose.yml expects it
mkdir -p ~/.config/gspread
cp /path/to/your-key.json ~/.config/gspread/service_account.json

mkdir -p .deploy
cp .deploy.example/.env .deploy/.env
# Edit .deploy/.env if needed (sheet logging, credentials path, etc.)
docker compose up -d
```

!!! tip
    Don't have a service account key yet? See [Google Sheets Setup](google-sheets-setup.md) first.

## Production deployment

- [Oracle Cloud Free Tier](deploy-oracle.md) — $0/month, always-on VM
- [Your own computer](deploy-selfhost.md) — $0, Tailscale Funnel or Cloudflare Tunnel

## Local development (without Docker)

See the [README](https://github.com/andgineer/dinary#local-development) for local development setup with `uv`.
