# Deploy on Your Own Computer

Run dinary on your Mac or PC and expose it to the internet via a tunnel. Free, always on while the computer is running, and aligns with the long-term architecture (desktop AI agent runs on the same machine).

## Pricing

| Resource | Cost |
|----------|------|
| Your computer | Already owned |
| Tailscale Funnel | $0 (free Personal plan) |
| Cloudflare Tunnel | $0 (free plan) |
| **Total** | **$0/month** |

## Prerequisites

- A Google service account JSON key — see [Google Sheets Setup](google-sheets-setup.md).
- A populated `.deploy/.env` with the variables you need (minimally `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH`; optionally `DINARY_SHEET_LOGGING_SPREADSHEET` for sheet logging).
- dinary running locally (see [README](https://github.com/andgineer/dinary#local-development)).

## Option A: Tailscale Funnel

Tailscale Funnel exposes a local port to the public internet over HTTPS. Simpler setup, but the URL is `*.ts.net` (no custom domain).

### 1. Install Tailscale

- **macOS**: `brew install tailscale` or download from [tailscale.com/download](https://tailscale.com/download)
- **Windows**: download from [tailscale.com/download](https://tailscale.com/download)
- **Linux**: `curl -fsSL https://tailscale.com/install.sh | sh`

Sign in and join your tailnet.

### 2. Enable Funnel

In the [Tailscale admin console](https://login.tailscale.com/admin/dns):

1. Enable **MagicDNS** (if not already enabled).
2. Enable **HTTPS** for your tailnet.

### 3. Start dinary

```bash
cd dinary
mkdir -p .deploy
cp .deploy.example/.env .deploy/.env
# Edit .deploy/.env if needed (optional `DINARY_SHEET_LOGGING_SPREADSHEET`
# for sheet logging, credentials path, etc.).
uv run uvicorn dinary.main:app --host 127.0.0.1 --port 8000
```

### 4. Expose via Funnel

In a separate terminal:

```bash
tailscale funnel 8000
```

Tailscale prints the public URL, e.g. `https://your-machine.your-tailnet.ts.net`. This URL is accessible from anywhere (phone, other devices) over HTTPS.

!!! note
    Funnel is in beta. DNS propagation may take a few minutes on first setup.

### 5. Keep running

To keep dinary running when you close the terminal:

=== "macOS (launchd)"

    ```bash
    # Create a plist or use a process manager like pm2/supervisord
    nohup uv run uvicorn dinary.main:app --host 127.0.0.1 --port 8000 &
    ```

=== "Linux (systemd)"

    See the [Oracle deployment guide](deploy-oracle.md) step 7 for a systemd service example.

=== "Windows"

    Use Task Scheduler or run as a Windows Service via [NSSM](https://nssm.cc/).

## Option B: Cloudflare Tunnel

Cloudflare Tunnel offers custom domains and Cloudflare Access for authentication. See the dedicated [Cloudflare Tunnel & Access Setup](cloudflare-setup.md) guide.

## When the computer is off

The PWA stores entries in IndexedDB when the server is unreachable. When you turn the computer back on and the tunnel reconnects, the PWA syncs all pending entries automatically on next open.

## Comparison

| | Tailscale Funnel | Cloudflare Tunnel |
|---|---|---|
| **Setup** | Simpler | More steps |
| **URL** | `*.ts.net` (fixed by Tailscale) | Your own domain |
| **Auth** | None built-in | Cloudflare Access (email OTP) |
| **Custom domain** | Not supported | Supported |
| **Status** | Beta | Stable |
