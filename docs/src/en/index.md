# Dinary

Track expenses, scan receipts (Serbia, Montenegro), analyze spending with AI.

Dinary server is a FastAPI backend that:

- Stores expenses in a local SQLite file in EUR (with the original amount and currency preserved for audit)
- Optionally mirrors every expense to a Google Sheets tab in RSD for pivot-table analytics
- Parses Serbian fiscal receipt QR codes (total + date)
- Serves a mobile PWA for quick expense entry in dinars
- Provides an offline-capable queue for entries without connectivity

<table>
<tr>
<td align="center" valign="top"><sub><b>Pick your category set on first launch</b></sub><br/><img src="images/screenshots/IMG_2583.PNG" width="280"/></td>
<td align="center" valign="top"><sub><b>Receipts classified by AI</b></sub><br/><img src="images/screenshots/IMG_2588.PNG" width="280"/><br/><img src="images/screenshots/IMG_2584.PNG" width="280"/></td>
<td align="center" valign="top"><sub><b>Entry in any currency — one tap</b></sub><br/><img src="images/screenshots/IMG_2585.PNG" width="280"/><br/><img src="images/screenshots/IMG_2586.PNG" width="280"/></td>
</tr>
</table>

## Quick start {#quick-start}

Dinary is a small server of your own, and the app on your phone is the page it
serves. It runs free on an Oracle Cloud Always Free VM, reached through your private
Tailscale network.

=== "With an AI agent"

    Have Claude Pro or Max, or ChatGPT Plus? Open the **Code** tab of the Claude
    desktop app, or **Codex** in the ChatGPT desktop app, and paste:

    ```text
    Install dinary on Oracle Cloud for me, following
    https://andgineer.github.io/dinary/agent-install/
    ```

    The agent tells you each step only you can do — signing up for Oracle and
    Tailscale, putting your key in a file — and does the rest.

=== "By hand"

    1. Deploy the server:
          - [Oracle Cloud Free Tier](deploy-oracle.md) — $0/month forever
          - [Your own computer](deploy-selfhost.md) — $0 (Tailscale Funnel or Cloudflare Tunnel)
    2. Set up HTTPS access — see the deployment guides above.
    3. [Install the PWA](pwa-install.md) on your phone.
    4. Optionally, [set up Google Sheets](google-sheets-setup.md) to get a row for
       every expense in a spreadsheet.
    5. Run `inv analytics` to talk to [your personal financial analyst](analytics.md).
