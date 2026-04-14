# Cloudflare Tunnel & Access Setup

Cloudflare provides HTTPS and authentication for dinary-server at no cost (free tier supports up to 50 users).

## Prerequisites

- A domain managed by Cloudflare (free plan is fine).
- dinary-server running on `http://localhost:8000`.

!!! note
    `cloudflared` is installed in step 1 below. Render and Railway users only need [step 6 (Cloudflare Access)](#6-set-up-cloudflare-access) — those platforms provide HTTPS natively, so no tunnel is needed.

## 1. Install cloudflared

=== "Ubuntu/Debian"
    ```bash
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
    sudo apt update && sudo apt install cloudflared
    ```

=== "macOS"
    ```bash
    brew install cloudflared
    ```

=== "Docker"
    ```bash
    docker pull cloudflare/cloudflared:latest
    ```

## 2. Create a tunnel

```bash
cloudflared tunnel login
cloudflared tunnel create dinary
```

This creates a tunnel and saves credentials to `~/.cloudflared/<TUNNEL_ID>.json`.

## 3. Configure the tunnel

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /home/<YOUR_USER>/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: dinary.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

Replace `<YOUR_USER>` with your Linux username (or use `/root/` if running as root).

## 4. Route DNS

```bash
cloudflared tunnel route dns dinary dinary.yourdomain.com
```

This creates a CNAME record pointing `dinary.yourdomain.com` to the tunnel.

## 5. Run the tunnel

```bash
cloudflared tunnel run dinary
```

To run as a system service:

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

Verify: open `https://dinary.yourdomain.com/api/health` in your browser.

## 6. Set up Cloudflare Access

1. Go to [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com/) → **Access** → **Applications**.
2. Click **Add an application** → **Self-hosted**.
3. Fill in:
      - **Application name**: Dinary
      - **Application domain**: `dinary.yourdomain.com`
      - **Session duration**: 30 days (or your preference)
4. Click **Next** → **Add a policy**:
      - **Policy name**: Allowed users
      - **Action**: Allow
      - **Include** → **Emails**: add the email addresses of allowed users
5. Click **Save**.

Now all requests to `dinary.yourdomain.com` require authentication via email OTP or Google OAuth.

## Pricing

| Component | Cost |
|-----------|------|
| Cloudflare DNS (free plan) | $0 |
| Cloudflare Tunnel | $0 |
| Cloudflare Access (up to 50 users) | $0 |
| **Total** | **$0** |
