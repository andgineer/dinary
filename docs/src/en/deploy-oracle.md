# Deploy on Oracle Cloud Free Tier

Oracle Cloud Always Free tier provides permanent VMs — enough to run dinary-server indefinitely at zero cost.

## Pricing

| Resource | Free Tier Allocation | Cost |
|----------|---------------------|------|
| AMD Micro VM | 2 instances, 1 OCPU + 1 GB RAM each | $0 forever |
| ARM Ampere A1 VM | Up to 4 OCPU, 24 GB RAM (shared pool — often unavailable) | $0 forever |
| Boot volume | 200 GB total | $0 |
| Outbound data | 10 TB/month | $0 |
| **Total** | | **$0/month** |

!!! tip "Which shape to choose"
    **AMD Micro** (`VM.Standard.E2.1.Micro`, 1 GB RAM) is recommended — it is almost always available because Oracle reserves a dedicated pool for free-tier accounts. 1 GB RAM is enough for FastAPI without Docker.

    **ARM Ampere A1** (`VM.Standard.A1.Flex`, up to 24 GB RAM) is more powerful but often unavailable ("Out of host capacity"). If you get one — great, otherwise use AMD Micro.

!!! warning
    Oracle may reclaim idle Always Free instances. Running a lightweight server like dinary keeps the instance active. If reclaimed, you can recreate it — your data lives in Google Sheets, not on the VM.

## Prerequisites

- A Google service account JSON key and spreadsheet ID — see [Google Sheets Setup](google-sheets-setup.md).
- An SSH key pair for connecting to the VM.

## 1. Create an account

1. Go to [cloud.oracle.com](https://cloud.oracle.com/) → **Sign Up**.
2. Select your home region (cannot be changed later).
3. Complete verification (credit card required but never charged for Always Free resources).

!!! tip "Region selection"
    Your home region is permanent. ARM instance availability varies by region. Community reports suggest **Ashburn**, **Phoenix**, **Frankfurt**, and **London** tend to have better ARM availability. However AMD Micro instances are available in all regions.

!!! note
    Account approval can take hours to days.

## 2. Set up networking (VCN)

Oracle VMs need a Virtual Cloud Network (VCN) with a public subnet and internet gateway. Create these **before** the VM:

1. Go to **Networking** → **Virtual Cloud Networks** → **Create VCN**.
      - **Name**: `dinary-vcn` (or any name)
      - **IPv4 CIDR Blocks**: `10.0.0.0/16`
      - Click **Create VCN**.

2. Inside the new VCN → **Subnets** → **Create Subnet**.
      - **Name**: `public-subnet`
      - **Subnet type**: Regional
      - **IPv4 CIDR Block**: `10.0.0.0/24`
      - **Subnet access**: **Public Subnet**
      - Click **Create Subnet**.

3. Inside the VCN → **Internet Gateways** → **Create Internet Gateway**.
      - **Name**: `internet-gw`
      - Click **Create Internet Gateway**.

4. Inside the VCN → **Route Tables** → click the default route table → **Add Route Rules**.
      - **Destination CIDR Block**: `0.0.0.0/0`
      - **Target Type**: Internet Gateway
      - **Target**: select `internet-gw`
      - Click **Add Route Rules**.

## 3. Create a VM

1. Go to **Compute** → **Instances** → **Create Instance**.
2. Configure:
      - **Image**: `Canonical Ubuntu 22.04 Minimal` (for AMD Micro — without `aarch64` in the name)
      - **Shape**: `VM.Standard.E2.1.Micro` — 1 OCPU, 1 GB RAM
      - **Capacity**: `On-demand capacity`
      - **Availability / Live migration**: `Let Oracle Cloud Infrastructure choose the best migration option`
      - **Networking**: select `dinary-vcn` → select `public-subnet` → check **Automatically assign public IPv4 address**
      - **SSH keys**: upload your public key
      - **Cloud-init script**: leave empty
3. Click **Create**.

!!! tip "ARM alternative"
    If ARM capacity is available, you can choose `Canonical Ubuntu 22.04 Minimal aarch64` + shape `VM.Standard.A1.Flex` (1 OCPU, 6 GB RAM). More RAM allows running Docker if desired. The rest of the setup is the same.

## 4. Connect to the VM

```bash
ssh ubuntu@<PUBLIC_IP>
```

## 5. Open firewall ports

!!! tip
    **Using Cloudflare Tunnel or Tailscale Funnel?** Skip this entire step — the tunnel connects outbound, so no inbound ports need to be opened.

If you need direct access to port 8000, open it in both Oracle firewalls:

### VCN Security List

1. Go to **Networking** → **Virtual Cloud Networks** → your VCN → **Security Lists**.
2. Add an ingress rule: Source `0.0.0.0/0`, Protocol TCP, Destination Port `8000`.

### OS Firewall

```bash
sudo iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save
```

## 6. Upload Google service account key

Run this **on your laptop** (not on the VM) to copy the key file to the server:

```bash
scp ~/.config/gspread/service_account.json ubuntu@<PUBLIC_IP>:~/credentials.json
```

See [Google Sheets Setup](google-sheets-setup.md) if you don't have the key file yet.

## 7. Install Python and dinary-server

Run these commands **on the VM** (SSH session):

```bash
sudo apt update && sudo apt install -y python3 python3-pip git

# Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env

# Clone and install
git clone https://github.com/andgineer/dinary-server.git
cd dinary-server
uv sync --no-dev

# Move the credentials file into the project directory
mv ~/credentials.json .
```

## 8. Run as a systemd service

Create a service file so dinary-server starts automatically and restarts on failure:

```bash
sudo tee /etc/systemd/system/dinary.service << 'EOF'
[Unit]
Description=dinary-server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/dinary-server
Environment=DINARY_GOOGLE_SHEETS_SPREADSHEET_ID=your-spreadsheet-id
ExecStart=/home/ubuntu/.local/bin/uv run uvicorn dinary.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Replace `your-spreadsheet-id` with your actual Google Sheets spreadsheet ID (the long string from the sheet URL).

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable dinary
sudo systemctl start dinary
```

Verify (wait 10-20 seconds for the first start — uv downloads dependencies on initial launch):

```bash
curl http://localhost:8000/api/health
```

## 9. Set up Tailscale Funnel (HTTPS access)

Tailscale Funnel exposes dinary-server to the internet over HTTPS without opening firewall ports or buying a domain.

### Install Tailscale on the VM

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

This prints a URL — open it in your browser to log in to Tailscale (create a free account if you don't have one).

### Enable HTTPS and Funnel

In the [Tailscale admin console](https://login.tailscale.com/admin/dns):

1. Enable **MagicDNS** (if not already enabled).
2. Enable **HTTPS** for your tailnet.

### Start Funnel

```bash
sudo tailscale funnel 8000
```

Tailscale prints the public URL, e.g. `https://instance-20260414.tail1234.ts.net`. This URL is accessible from anywhere (your phone, other devices) over HTTPS.

!!! warning "First launch: wait up to 10 minutes"
    On first launch, Tailscale provisions a TLS certificate and propagates DNS. The URL may return `ERR_SSL_PROTOCOL_ERROR` for several minutes. Wait and retry — it will start working.

### Run Funnel as a service

To keep Funnel running after you close the SSH session:

```bash
sudo tee /etc/systemd/system/tailscale-funnel.service << 'EOF'
[Unit]
Description=Tailscale Funnel for dinary
After=tailscaled.service

[Service]
Type=simple
ExecStart=/usr/bin/tailscale funnel 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable tailscale-funnel
sudo systemctl start tailscale-funnel
```

Verify: open `https://<your-machine>.ts.net/api/health` in your phone's browser.

!!! tip "Alternative: Cloudflare Tunnel"
    If you have a domain on Cloudflare DNS, you can use [Cloudflare Tunnel & Access](cloudflare-setup.md) instead — it provides a custom domain and email-based authentication. Requires migrating your domain's DNS to Cloudflare (full setup, free).

## Maintenance

- **Logs**: `sudo journalctl -u dinary -f`
- **Update**: `cd ~/dinary-server && git pull && uv sync --no-dev && sudo systemctl restart dinary`
- **Restart**: `sudo systemctl restart dinary`
- **Status**: `sudo systemctl status dinary`
