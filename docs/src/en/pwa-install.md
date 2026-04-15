# Installing the PWA

Dinary works as a Progressive Web App (PWA) — an app-like experience in your browser,
installed directly from the website. No App Store or Google Play needed.

## 1. Find your server URL

Your URL depends on how you deployed dinary-server:

- **Tailscale Funnel**: `https://<your-machine>.<tailnet>.ts.net` (shown when you ran `tailscale funnel 8000`)
- **Cloudflare Tunnel**: `https://dinary.yourdomain.com` (the domain you configured)

### If using Tailscale Funnel

Tailscale Funnel URLs are public — anyone with the link can access them. However, to find the URL you need the machine's MagicDNS name. The easiest way to get it on your phone:

1. Install **Tailscale** on your phone ([App Store](https://apps.apple.com/app/tailscale/id1470499037) / [Google Play](https://play.google.com/store/apps/details?id=com.tailscale.ipn)).
2. Log in with the same account you used on the server.
3. In the Tailscale app, find your server machine — its name is the MagicDNS hostname.
4. Open `https://<that-name>/api/health` in the browser to verify it works.

!!! tip
    You only need the Tailscale app to look up the URL. After that, the Funnel URL works in any browser — Tailscale doesn't need to be running on your phone.

## 2. Install the PWA

### Android (Chrome)

1. Open your server URL in **Chrome**.
2. If prompted by Cloudflare Access, log in with your email (one-time).
3. Tap the browser menu (**⋮**) → **Add to Home Screen** → **Add**.
4. The Dinary icon appears on your home screen.

!!! tip
    Chrome may show a banner at the bottom: **"Add Dinary to Home screen"** — tap it for a one-step install.

### iOS (Safari)

1. Open your server URL in **Safari** (PWA install only works in Safari on iOS).
2. If prompted by Cloudflare Access, log in with your email (one-time).
3. Tap the **Share** button (□↑) → **Add to Home Screen** → **Add**.
4. The Dinary icon appears on your home screen.

!!! warning
    iOS does not support PWA install from Chrome or Firefox — you **must** use Safari.

## Using the app

- **Manual entry**: enter amount, select category, add an optional comment, and tap Save.
- **QR scan**: tap "Scan QR", point the camera at a Serbian fiscal receipt QR code. The amount and date are pre-filled — pick a category and save.
- **Offline**: if you have no internet, entries are queued locally and sync automatically when connectivity returns. A badge in the header shows the number of queued entries.

## Re-authentication (Cloudflare Access only)

If you use Cloudflare Access, sessions last 30 days by default. When the session expires:

- The app will prompt you to re-authenticate.
- Open the app in your browser — Cloudflare shows the login page.
- After login, any queued entries sync automatically.
