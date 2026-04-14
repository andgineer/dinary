# Deploy on Render

Render provides container hosting with GitHub auto-deploy. The free tier works for dinary-server but has cold starts.

## Pricing

| Plan | Cost | RAM | Always On | Cold Start |
|------|------|-----|-----------|------------|
| Free | $0/month | 512 MB | No — spins down after 15 min inactivity | ~30 seconds |
| Starter | $7/month | 512 MB | Yes | None |

!!! note
    The free tier spins down after 15 minutes of inactivity. The first request after spin-down takes ~30 seconds. For a personal expense tracker used a few times a day, this is noticeable but tolerable. The $7/month Starter plan eliminates cold starts.

## 1. Prerequisites

- A GitHub account with the `dinary-server` repo pushed.
- A Google service account JSON key and spreadsheet ID — see [Google Sheets Setup](google-sheets-setup.md).

## 2. Create a Web Service

1. Go to [render.com](https://render.com/) → **Sign up** (GitHub login).
2. Click **New** → **Web Service**.
3. Connect your `dinary-server` GitHub repository.
4. Configure:
      - **Name**: `dinary-server`
      - **Region**: pick the closest to you
      - **Runtime**: Docker
      - **Instance Type**: Free (or Starter for $7/month)
5. Click **Create Web Service**.

## 3. Set environment variables

In the Render dashboard → your service → **Environment**:

| Variable | Value |
|----------|-------|
| `DINARY_GOOGLE_SHEETS_SPREADSHEET_ID` | your spreadsheet ID |
| `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` | `/etc/secrets/credentials.json` |
| `DINARY_LOG_JSON` | `true` |

## 4. Add the service account key

Render supports **Secret Files**:

1. Go to your service → **Environment** → **Secret Files**.
2. Add a file:
      - **Filename**: `/etc/secrets/credentials.json`
      - **Contents**: paste the full JSON key contents
3. Save.

## 5. Deploy

Render auto-deploys on every push to your default branch. You can also trigger a manual deploy from the dashboard.

Verify: open `https://dinary-server.onrender.com/api/health`.

## 6. Custom domain + Cloudflare Access

To use your own domain with Cloudflare Access authentication:

1. In Render → your service → **Settings** → **Custom Domains** → add `dinary.yourdomain.com`.
2. In Cloudflare DNS, add a CNAME: `dinary` → `dinary-server.onrender.com` (proxied).
3. Follow the [Cloudflare Access setup](cloudflare-setup.md#6-set-up-cloudflare-access) to add authentication.

!!! note
    With Render, you don't need Cloudflare Tunnel — Render already provides HTTPS. You only need Cloudflare for DNS + Access authentication.

## Maintenance

- **Logs**: Render dashboard → your service → **Logs**.
- **Update**: push to GitHub — auto-deploys.
- **Restart**: dashboard → **Manual Deploy** → **Clear build cache & deploy**.
