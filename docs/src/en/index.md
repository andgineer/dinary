# Dinary

See what went into the shopping bag. Track expenses, scan receipts (Serbia,
Montenegro), analyze spending with AI.

<table>
<tr>
<td align="center" valign="top"><sub><b>Pick your category set on first launch</b></sub><br/><img src="images/screenshots/IMG_2583.PNG" width="280"/></td>
<td align="center" valign="top"><sub><b>Receipts classified by AI</b></sub><br/><img src="images/screenshots/IMG_2588.PNG" width="280"/></td>
<td align="center" valign="top"><sub><b>Entry in any currency — one tap</b></sub><br/><img src="images/screenshots/IMG_2585.PNG" width="280"/></td>
</tr>
</table>

- **Know what you bought**: a supermarket receipt split into item-level spending,
  with categories the app learns from your corrections.
- **Capture it while you remember**: scan a QR code or enter an expense on your
  phone, even offline.
- **Explore spending your way**: categories, trips, tags and currencies, with an AI
  analyst on your computer.

[Everything it does](#what-it-does)

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

## What it does {#what-it-does}

Dinary server is a FastAPI backend that:

- Stores expenses in a local SQLite file in EUR (with the original amount and currency preserved for audit)
- Optionally mirrors every expense to a Google Sheets tab in RSD for pivot-table analytics
- Parses Serbian fiscal receipt QR codes (total + date)
- Serves a mobile PWA for quick expense entry in dinars
- Provides an offline-capable queue for entries without connectivity

<table>
<tr>
<td align="center" valign="top"><sub><b>Review receipt categories</b></sub><br/><img src="images/screenshots/IMG_2584.PNG" width="280"/></td>
<td align="center" valign="top"><sub><b>Quick entry in any currency</b></sub><br/><img src="images/screenshots/IMG_2586.PNG" width="280"/></td>
</tr>
</table>

## Under the hood {#under-the-hood}

**Corrections become reusable rules.** Known items use stored classification
rules; unfamiliar or uncertain items go to an LLM. A user correction takes
precedence over the model and applies across branches of the same retail chain.
Classification failures remain recoverable through retries and review.
The [classification design](https://github.com/andgineer/dinary/blob/main/specs/reference/classification-pipeline.md) explains
confidence, alternatives, and manual resolution.

Provider selection and failover are handled by
[llmbroker](https://github.com/andgineer/llmbroker), a standalone library for
applications that need a pool of LLM providers. Receipt logic stays in Dinary.

**A saved expense outlives a failed network call.** IndexedDB holds offline
entries, and the QR scanner is bundled for offline use. On the server, the
SQLite ledger is the source of truth. Google Sheets export runs through a
retryable queue, so a spreadsheet outage does not prevent expense capture.
See the [offline design](https://github.com/andgineer/dinary/blob/main/specs/reference/pwa-offline.md) and
[Sheets integration](https://github.com/andgineer/dinary/blob/main/specs/reference/sheets.md).

**Analysis has a separate home.** The local analytics app uses DuckDB to query a
read-only ledger replica, keeping heavy analytics dependencies off the capture
server. Its AI can query spending and configure views without editing the
ledger. The [analytics design](https://github.com/andgineer/dinary/blob/main/specs/reference/analytics-ai.md) covers the
dashboard and MCP interface.

**Small, frequent lookups shaped the storage layer.** In an early synthetic
workload, 5,000 mapping lookups took 2.0 seconds with direct SQL and 19.9 seconds
through Ibis. That comparison led to SQL files and migrations for the
transactional backend. The
[architecture record](https://github.com/andgineer/dinary/blob/main/specs/reference/architecture.md#sql-files-over-ibis)
documents the workload and trade-offs; the result describes that benchmark,
not whole-application performance.
