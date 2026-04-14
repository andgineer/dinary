# Deploy on Railway

Railway runs Docker containers with usage-based pricing and GitHub auto-deploy.

## Pricing

| Component | Cost |
|-----------|------|
| Trial credit | $5 free (one-time, no credit card) |
| Hobby plan | $5/month (includes $5 usage credit) |
| Compute (after credit) | ~$0.000463/min per vCPU |
| RAM (after credit) | ~$0.000231/min per GB |

For dinary-server (~20 requests/day, minimal CPU), estimated usage is well within the $5/month credit — **effectively $5/month total with no overage**.

!!! tip
    Unlike Render free tier, Railway does not spin down services — no cold starts.

## 1. Prerequisites

- A GitHub account with the `dinary-server` repo pushed.
- A Google service account JSON key and spreadsheet ID — see [Google Sheets Setup](google-sheets-setup.md).

## 2. Create a project

1. Go to [railway.app](https://railway.app/) → **Sign up** (GitHub login).
2. Click **New Project** → **Deploy from GitHub repo**.
3. Select your `dinary-server` repository.
4. Railway auto-detects the Dockerfile and starts building.

## 3. Set environment variables

In the Railway dashboard → your service → **Variables**:

| Variable | Value |
|----------|-------|
| `DINARY_GOOGLE_SHEETS_SPREADSHEET_ID` | your spreadsheet ID |
| `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` | `/app/credentials.json` |
| `DINARY_LOG_JSON` | `true` |

## 4. Add the service account key

Railway does not have a built-in secret files feature. Options:

=== "Base64 in env var (simplest)"
    1. Encode the key: `base64 -i credentials.json`
    2. Add env var `DINARY_GOOGLE_CREDENTIALS_BASE64` with the encoded value.
    3. dinary-server detects this variable on startup and writes the decoded JSON to the path specified by `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` — no code changes needed.

=== "Volume mount"
    1. In Railway dashboard → your service → **Volumes** → add a volume mounted at `/data`.
    2. Use Railway CLI to copy the file: `railway run cp credentials.json /data/`
    3. Set `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH=/data/credentials.json`.

## 5. Configure networking

1. In Railway dashboard → your service → **Settings** → **Networking**.
2. Click **Generate Domain** to get a `*.up.railway.app` URL.
3. Or add a custom domain: `dinary.yourdomain.com`.

## 6. Custom domain + Cloudflare Access

1. In Railway → **Settings** → **Custom Domain** → add `dinary.yourdomain.com`.
2. In Cloudflare DNS, add a CNAME: `dinary` → the Railway-provided target (proxied).
3. Follow the [Cloudflare Access setup](cloudflare-setup.md#6-set-up-cloudflare-access) to add authentication.

!!! note
    Like Render, Railway provides HTTPS natively — no Cloudflare Tunnel needed. Use Cloudflare only for DNS + Access.

## Maintenance

- **Logs**: Railway dashboard → your service → **Logs** (real-time).
- **Update**: push to GitHub — auto-deploys.
- **Restart**: dashboard → **Restart** button.
- **Usage**: dashboard → **Usage** tab shows compute/RAM/bandwidth.
