# Installing the PWA

Dinary works as a Progressive Web App (PWA) — an app-like experience in your browser,
installed directly from the website. No App Store or Google Play needed.

## Android (Chrome)

1. Open `https://dinary.yourdomain.com` in **Chrome**.
2. If prompted by Cloudflare Access, log in with your email (one-time).
3. Tap the browser menu (**⋮**) → **Add to Home Screen** → **Add**.
4. The Dinary icon appears on your home screen.

!!! tip
    Chrome may show a banner at the bottom: **"Add Dinary to Home screen"** — tap it for a one-step install.

## iOS (Safari)

1. Open `https://dinary.yourdomain.com` in **Safari** (PWA install only works in Safari on iOS).
2. If prompted by Cloudflare Access, log in with your email (one-time).
3. Tap the **Share** button (□↑) → **Add to Home Screen** → **Add**.
4. The Dinary icon appears on your home screen.

!!! warning
    iOS does not support PWA install from Chrome or Firefox — you **must** use Safari.

## Using the app

- **Manual entry**: enter amount, select category, add an optional comment, and tap Save.
- **QR scan**: tap "Scan QR", point the camera at a Serbian fiscal receipt QR code. The amount and date are pre-filled — pick a category and save.
- **Offline**: if you have no internet, entries are queued locally and sync automatically when connectivity returns. A badge in the header shows the number of queued entries.

## Re-authentication

Cloudflare Access sessions last 30 days by default. When the session expires:

- The app will prompt you to re-authenticate.
- Open the app in your browser — Cloudflare shows the login page.
- After login, any queued entries sync automatically.
