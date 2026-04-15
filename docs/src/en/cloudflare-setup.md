# Cloudflare Tunnel & Access Setup

Cloudflare Tunnel creates an encrypted connection from the machine running dinary-server to Cloudflare's network. This gives you HTTPS and a custom domain without opening firewall ports. Cloudflare Access adds email-based authentication (free for up to 50 users).

All commands in steps 1-5 are executed **on the machine where dinary-server runs** (Oracle Cloud VM, your Mac/PC, etc.).

## Prerequisites

- A domain managed by Cloudflare (free plan is fine). If you don't have one, you can buy a domain directly in Cloudflare (~$10/year for `.com`).
- dinary-server running on `http://localhost:8000` on the target machine.

## 1. Install cloudflared on the server

SSH into your server (or open a terminal on your Mac/PC) and install `cloudflared`:

=== "Ubuntu/Debian (Oracle Cloud VM)"
    ```bash
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
    sudo apt update && sudo apt install cloudflared
    ```

=== "macOS (self-hosted)"
    ```bash
    brew install cloudflared
    ```

## 2. Create a tunnel

```bash
cloudflared tunnel login
```

This opens a browser for Cloudflare authentication. If running on a headless server (Oracle VM), copy the URL it prints and open it on your laptop.

```bash
cloudflared tunnel create dinary
```

Saves credentials to `~/.cloudflared/<TUNNEL_ID>.json`.

## 3. Configure the tunnel

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /home/ubuntu/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: dinary.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

Replace `/home/ubuntu/` with your actual home directory (e.g., `/Users/yourname/` on macOS).

## 4. Route DNS

```bash
cloudflared tunnel route dns dinary dinary.yourdomain.com
```

This creates a CNAME record pointing `dinary.yourdomain.com` to the tunnel.

## 5. Run the tunnel

```bash
cloudflared tunnel run dinary
```

To run as a system service (so it survives reboots):

=== "Linux (Oracle VM)"
    ```bash
    sudo cloudflared service install
    sudo systemctl enable cloudflared
    sudo systemctl start cloudflared
    ```

=== "macOS"
    ```bash
    sudo cloudflared service install
    sudo launchctl start com.cloudflare.cloudflared
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
