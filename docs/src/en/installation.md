# Installation

## Quick start (Docker, local development)

The simplest way to run dinary-server locally is with Docker:

```bash
git clone https://github.com/andgineer/dinary-server.git
cd dinary-server

# Place your Google service account key (see Google Sheets Setup)
cp /path/to/your-key.json credentials.json

cp .env.example .env
# Edit .env: set GOOGLE_SHEETS_SPREADSHEET_ID
docker compose up -d
```

!!! tip
    Don't have a service account key yet? See [Google Sheets Setup](google-sheets-setup.md) first.

## Production deployment

- [Oracle Cloud Free Tier](deploy-oracle.md) — $0/month, always-on VM
- [Your own computer](deploy-selfhost.md) — $0, Tailscale Funnel or Cloudflare Tunnel

## Local development (without Docker)

See the [README](https://github.com/andgineer/dinary-server#local-development) for local development setup with `uv`.
