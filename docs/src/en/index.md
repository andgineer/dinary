# Dinary server

Server for [Dinary - your dinar diary](https://github.com/andgineer/dinary).

Track expenses, scan receipts, analyze spending with AI.

Dinary-server is a FastAPI backend that:

- Stores expenses in Google Sheets (with automatic EUR conversion)
- Parses Serbian fiscal receipt QR codes (total + date)
- Serves a mobile PWA for quick expense entry
- Provides an offline-capable queue for entries without connectivity

### Quick start

1. [Set up Google Sheets](google-sheets-setup.md) — create a service account and spreadsheet.
2. Deploy the server:
      - [Oracle Cloud Free Tier](deploy-oracle.md) — $0/month forever
      - [Render](deploy-render.md) — $0-7/month
      - [Railway](deploy-railway.md) — $5/month
3. Set up [Cloudflare Tunnel & Access](cloudflare-setup.md) for HTTPS and authentication.
4. [Install the PWA](pwa-install.md) on your phone.

!!! info "About"
    ![About](images/about.jpg)
    [About][dinary.__about__]
