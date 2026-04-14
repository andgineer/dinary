# Deploy on Oracle Cloud Free Tier

Oracle Cloud Always Free tier provides a permanent ARM VM — enough to run dinary-server indefinitely at zero cost.

## Pricing

| Resource | Free Tier Allocation | Cost |
|----------|---------------------|------|
| ARM Ampere A1 VM | Up to 4 OCPU, 24 GB RAM | $0 forever |
| Boot volume | 200 GB total | $0 |
| Outbound data | 10 TB/month | $0 |
| **Total** | | **$0/month** |

!!! warning
    Oracle may reclaim idle Always Free instances. Running a lightweight server like dinary keeps the instance active. If reclaimed, you can recreate it — your data lives in Google Sheets, not on the VM.

## Prerequisites

- A Google service account JSON key and spreadsheet ID — see [Google Sheets Setup](google-sheets-setup.md).
- An SSH key pair for connecting to the VM.

## 1. Create an account

1. Go to [cloud.oracle.com](https://cloud.oracle.com/) → **Sign Up**.
2. Select your home region (cannot be changed later — pick one close to you).
3. Complete verification (credit card required but never charged for Always Free resources).

!!! note
    Account approval can take hours to days. If your account is stuck in provisioning, try a different region.

## 2. Create a VM

1. Go to **Compute** → **Instances** → **Create Instance**.
2. Configure:
      - **Image**: Ubuntu 22.04 (or 24.04) Minimal — ARM
      - **Shape**: VM.Standard.A1.Flex — 1 OCPU, 6 GB RAM (plenty for dinary)
      - **Networking**: create a VCN with a public subnet, assign a public IP
      - **SSH keys**: upload your public key
3. Click **Create**.

## 3. Open firewall ports

!!! tip
    **Using Cloudflare Tunnel (recommended)?** Skip this entire step — the tunnel connects outbound, so no inbound ports need to be opened.

If you need direct access to port 8000 (without Cloudflare Tunnel), open it in both Oracle firewalls:

### VCN Security List

1. Go to **Networking** → **Virtual Cloud Networks** → your VCN → **Security Lists**.
2. Add an ingress rule: Source `0.0.0.0/0`, Protocol TCP, Destination Port `8000`.

### OS Firewall

```bash
sudo iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save
```

## 4. Install Docker

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER
```

Log out and back in for the group change to take effect.

## 5. Deploy dinary-server

```bash
git clone https://github.com/andgineer/dinary-server.git
cd dinary-server

# Place Google service account key (see Google Sheets Setup guide)
cp /path/to/your-key.json credentials.json

# Configure
cp .env.example .env
# Edit .env: set GOOGLE_SHEETS_SPREADSHEET_ID

# Build and start
docker compose up -d
```

Verify:

```bash
curl http://localhost:8000/api/health
```

## 6. Set up Cloudflare Tunnel

Follow the [Cloudflare Tunnel & Access Setup](cloudflare-setup.md) guide to expose the server over HTTPS with authentication.

## Maintenance

- **Logs**: `docker compose logs -f`
- **Update**: `git pull && docker compose up -d --build`
- **Restart**: `docker compose restart`
