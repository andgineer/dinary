You are installing dinary, a self-hosted expense tracker, for the person you are
talking to. When you are done, an Oracle Cloud Always Free VM runs it, it is
reachable only inside the user's private Tailscale network at
`https://dinary.<tailnet>.ts.net/`, and the user's phone opens it as a home-screen
app: they enter expenses in any currency and scan fiscal receipt QR codes (Serbia,
Montenegro), and an LLM sorts each receipt's items into categories. The expenses
live in a SQLite database on that VM. All of it is free.

If you are reading this as a web page, get the exact text first — a page fetched
through a summarising tool loses details these steps depend on:

```bash
curl -fsSL https://raw.githubusercontent.com/andgineer/dinary/main/docs/includes/agent-install.md
```

## How to work with the user

- The user has read nothing but the one-line prompt they gave you. Everything they
  need to know comes from you, in the language of that prompt.
- Begin with a short overview: what you will set up, which steps only they can do,
  that you will ask their approval for commands as you go, and what they need at
  hand — a credit or debit card for Oracle's identity check (Always Free is never
  charged), their phone, and a Google account for a free LLM key.
- Hand them one step at a time: exactly which page to open, what to click or type,
  and what to tell you back. Wait until they say it is done before going on.
- Between their steps, say in a sentence what you are about to do, then report what
  happened.
- Never ask for a password or an API key in the chat, and never print the keys file:
  either would stay in the conversation's record. Secrets go into a file on their
  computer (step 6), and you check that it is filled without printing it.
- When something fails in a way these instructions do not cover, tell the user what
  happened and what you propose before you do it.

## What the user already has

Before step 1, ask what they already have, and skip what is done:

- **An Oracle Cloud account**: skip step 3.
- **A Tailscale account**: skip creating it in step 5, but still have them check that
  HTTPS Certificates are on. Their phone may already have the Tailscale app.
- **An earlier dinary install**: a checkout whose `.deploy/.env` names a host. It is
  at `~/dinary` unless they put it elsewhere, so ask. Work from that checkout
  instead of cloning another: its keys are already filled in. If `git status` there
  shows changes, or it is not on `main`, ask before you pull. Read what it names with
  `grep -E '^DINARY_(DEPLOY_HOST|TUNNEL|REPLICA_HOST)=' .deploy/.env`, check with
  `ssh <host> true` whether that VM answers — if it does not, have the user look at
  it in the Oracle Cloud console → Compute → Instances: a **Stopped** VM only needs
  **Start**, and keeps its address and data — and ask which of three cases this is:
    - **a session that stopped partway** — a usage limit, a closed app: continue
      from the first step not done. If `.deploy/old-vm/` exists, it was a move;
      `.deploy/old-vm/host` holds the old VM's address.
    - **an update of the same VM**: in the checkout, `git pull --ff-only`,
      `uv sync --inexact`, `uv run inv deploy --ref=main`, then step 8; nothing
      else
    - **a move to a new VM**, because the old one is gone or being replaced: go
      through the steps, following their **Moving from an old VM** notes, with
      `<old-host>` the host the keys file names now. These instructions cover the
      Tailscale setup only: if `DINARY_TUNNEL` is `cloudflare` or `none`, stop and
      tell the user.

Below, `<checkout>` is that checkout, or `~/dinary` for a first install.

## This is not a development task

The checkout contains `AGENTS.md` and `CLAUDE.md` for developers changing the code.
Their rules do not apply to an install, an update or a move: do not run the test
environment script, the lint or the test commands they require, and do not change
any tracked file. The user asking you for one of these is their approval to set up
and deploy to their own VM.

## 0. Check where you are running

- You need a shell on the user's own computer, because the checkout and its keys
  stay there: every later update runs from it, and every deploy saves a copy of the
  database there. If you run in a remote sandbox or a cloud session, stop and tell
  the user to start you on their computer instead.
- Run `uname -s`. `Darwin` or `Linux` (WSL included) — go on. Anything else, such as
  Git Bash, MSYS or PowerShell on Windows, cannot run the deploy commands. Tell the
  user to install WSL: run `wsl --install` in PowerShell opened as administrator,
  restart, and finish the Ubuntu setup it opens. Then they start a new session of you
  inside WSL — in the Claude desktop app's Code tab, by choosing the WSL environment;
  in Codex in the ChatGPT desktop app, with Settings → Agent environment → Windows
  Subsystem for Linux and a restart of the app — and paste the same prompt again.
  Stop there.
- You need network access, including outbound ssh, and you will write to
  `<checkout>` and `~/.ssh`. If your sandbox asks the user to approve those, tell
  them to expect it.

## 1. Tools and the checkout

- You need `git`, `ssh`, `ssh-keygen`, `curl` and `uv`. Check each by running it
  (`git --version`, not `which git`: macOS ships a stub that only offers to install
  git). Install what is missing:
    - uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`. It is not on the PATH
      of the shell you have; your later commands may each start a new shell, so call
      it as `~/.local/bin/uv` or prefix them with `source ~/.local/bin/env &&`
    - git on macOS: `xcode-select --install`, which opens a dialog the user must
      confirm
    - on Linux or WSL: `sudo apt-get install -y git openssh-client curl` — when sudo
      needs a password your shell cannot type, have the user run that command
      themselves
- The checkout:
    - an earlier install's: `git -C <checkout> pull --ff-only`
    - none yet, and `~/dinary` does not exist:
      `git clone https://github.com/andgineer/dinary.git ~/dinary`
    - `~/dinary` exists but is not a checkout of `github.com/andgineer/dinary`:
      stop and ask the user where to put it

    Run every later command from the checkout, starting with `uv sync --inexact`,
    which installs what is missing and removes nothing an earlier install added.

## 2. The ssh key

Create a key used only for this VM, without a passphrase — the deploy commands run
without a terminal and cannot type one:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
test -f ~/.ssh/dinary || ssh-keygen -t ed25519 -N "" -C dinary -f ~/.ssh/dinary
```

Tell the user it is their key to the VM. Only `~/.ssh/dinary.pub` — the public key —
is ever shown or pasted.

## 3. The Oracle Cloud account — the user's step

Oracle allows one account per person and checks it against a card, so only the user
can create it. Send them to <https://signup.oraclecloud.com/> and tell them:

- their name and address must match the card's billing address exactly; a mismatch
  is the most common reason a signup is refused
- the card verifies identity, and may show a small temporary hold; the Always Free
  resources used here are never charged
- the **home region** they pick is permanent, and the VM has to be created in it, so
  they should pick one near them

Wait until they are signed in to the Oracle Cloud console.

The first sign-in enrols their phone as the second sign-in factor. Have them generate
a bypass code right away: Profile menu → **User settings** → **Security** →
**Bypass codes** → **Generate**, and keep it off the phone — on paper or in a
password manager, never in the chat. On their own account they are its only
administrator, so if that phone is lost, the code is the way back in short of
Oracle support. Each code works once and never expires.

## 4. The VM

**Moving from an old VM.** Do everything that needs the old VM now, before the new
one exists:

1. Ask whether anything besides dinary runs on the old VM, and whether any of it is
   reached by the old VM's Tailscale name. If so, the new VM cannot take that name
   without cutting the other thing off: it gets a name of its own in step 7, and
   the app on the phone is installed afresh in step 9.
2. Record the old VM, so a session that stops partway can resume:

    ```bash
    mkdir -p .deploy/old-vm
    echo '<old-host>' > .deploy/old-vm/host
    ```

3. If `<old-host>` answers, read its Tailscale name — the first label of
   `Self.DNSName` in `ssh <old-host> tailscale status --json` — and move the
   database. First the user opens the app on the phone, online, and checks that
   nothing is waiting to be sent: the app keeps entries made without a connection
   and shows a strip reading `N receipts queued` until it has sent them. If the strip
   is there, they keep the app open until it disappears. Then stop the app there for
   good — and its replication, if it has any — so nothing is written after the copy,
   and copy it:

    ```bash
    ssh <old-host> 'sudo systemctl disable --now dinary; sudo systemctl disable --now litestream 2>/dev/null; true'
    ssh <old-host> 'sqlite3 /home/ubuntu/dinary/data/dinary.db ".backup /tmp/dinary-move.db"'
    scp <old-host>:/tmp/dinary-move.db .deploy/old-vm/dinary.db
    uv run python -c 'import sqlite3; db = sqlite3.connect(".deploy/old-vm/dinary.db"); print(db.execute("pragma integrity_check").fetchone()[0], db.execute("select count(*) from expenses").fetchone()[0])'
    ```

    The last line prints `ok` and the number of expenses; keep the number for
    step 8.

    If `<old-host>` does not answer, first have the user check Compute → Instances:
    a **Stopped** VM is started with **Start** and answers again within minutes —
    then this is an update, not a move.
    Only a VM that is gone means the database has to come from a backup:
    - with a replica (`DINARY_REPLICA_HOST` is set):
      `uv run inv restore-replica -o .deploy/old-vm/dinary.db --yes`
    - without one: the newest `data/backups/pre-deploy-*/dinary.db` in the checkout
      — every deploy saves one — copied to `.deploy/old-vm/dinary.db`. Tell the user
      how old it is: expenses entered after it are lost.

    Check the copy with the same last line.

4. A free account holds at most two VM.Standard.E2.1.Micro instances. Have the user
   check Compute → Instances: if two already exist, the new one cannot be created
   beside them — on Pay As You Go it would be billed. Then the old VM has to go
   first: only if nothing else runs on it (item 1), the user terminates it in the
   console. Otherwise stop and agree with the user which VM to give up. To be sure
   which instance it is, read its name in the console from the VM itself:
   `ssh <old-host> 'curl -s -H "Authorization: Bearer Oracle" http://169.254.169.254/opc/v2/instance/displayName'`.

**The network.** A new account has none, and the VM needs one that reaches the
internet. Have the user open Networking → Virtual cloud networks → **Start VCN
Wizard** → **Create VCN with Internet Connectivity**, and accept the defaults. An
account that already has such a network uses it.

The VM must be:

- a compute instance in the home region
- image **Canonical Ubuntu 22.04 Minimal** — the image the deploy runs on
- shape **VM.Standard.E2.1.Micro**, which is Always Free-eligible; the shape picker
  lists it under **Specialty and previous generation**
- in that network's public subnet, with **Automatically assign public IPv4 address**
  on
- given the contents of `~/.ssh/dinary.pub` under **Paste public keys**
- otherwise left at the defaults

Oracle's console changes its layout, so go by these requirements rather than
remembered clicks. If you can operate the user's browser, you may fill in the form
yourself once they are signed in; show them what you chose before you press
**Create**. Otherwise guide them through it, field by field. This shape exists in a
single availability domain of the region, so if it is out of capacity there is
nothing else to pick: it has to be retried later. Never choose a shape that is not
Always Free.

When the instance shows **Running**, have the user tell you its public IP address —
it is not a secret. Make ssh use the key for it, and accept the VM's host key once,
because the deploy commands cannot answer that question:

```bash
printf '\nHost <IP>\n  User ubuntu\n  IdentityFile ~/.ssh/dinary\n  IdentitiesOnly yes\n' >> ~/.ssh/config
chmod 600 ~/.ssh/config
ssh -o StrictHostKeyChecking=accept-new ubuntu@<IP> true
```

A new VM can take a minute or two before ssh answers: retry on a timeout or a
refused connection. `Permission denied (publickey)` is not a boot delay — the VM was
given a different key; check what was pasted in the form. If ssh still times out
five minutes after **Running**, the subnet has no route to the internet: on the
instance's page, follow its subnet to the virtual cloud network and add one — the
console offers **Connect public subnet to internet** for this.

## 5. Tailscale — the user's steps

One at a time:

1. Create a Tailscale account at <https://login.tailscale.com/start>. The Personal
   plan is free; signing in with Google is enough.
2. In the admin console's DNS page, <https://login.tailscale.com/admin/dns>, turn on
   **HTTPS Certificates**; MagicDNS has to be on too, and is on by default. The app
   is published with `tailscale serve`, which needs both.
3. Install the Tailscale app on their phone and sign in with the same account.

Step 7 joins the VM to their network under the name `dinary`. **Moving from an old
VM** whose name the new one takes (step 4, item 1): two machines cannot share a
name, so the user now removes the old machine on
<https://login.tailscale.com/admin/machines> — the menu at the right of its row,
**Remove**.

## 6. The keys file

In `<checkout>`, create `.deploy/.env` from the template, unless it already
exists — never overwrite an existing one:

```bash
mkdir -p .deploy
[ -e .deploy/.env ] || cp .deploy.example/.env .deploy/.env
chmod 600 .deploy/.env
```

Point it at the new VM — this replaces the template's placeholder, or an earlier
install's old address — without opening the file:

```bash
sed -i.bak 's|^DINARY_DEPLOY_HOST=.*|DINARY_DEPLOY_HOST=ubuntu@<IP>|' .deploy/.env && rm .deploy/.env.bak
```

An earlier install's file already holds the user's secrets and settings: change
nothing else in it, and go to the check at the end of this step. Its currency
setting in particular must stay as it is: the database keeps the currency it was
created with, and the app refuses to start if the setting disagrees.

For a first install, ask the user two questions:

- **Which currency to keep the books in.** Every amount is stored in it. The first
  start fixes it for good; it cannot be changed later. EUR is the default; for
  another, uncomment and set `DINARY_ACCOUNTING_CURRENCY` to its ISO code.
- **Which currency to enter expenses in by default.** RSD is the default, and it can
  be changed at any time; for another, uncomment and set `DINARY_APP_CURRENCY`.

Then the user adds their LLM key, which sorts receipt items into categories. The
template has the key lines commented out; uncomment the one they need:

```bash
sed -i.bak 's|^# GEMINI_API_KEY=$|GEMINI_API_KEY=|' .deploy/.env && rm .deploy/.env.bak
```

Open the file for them in an editor they can use, in the background — on macOS
`open -e <checkout>/.deploy/.env`, in WSL
`notepad.exe "$(wslpath -w <checkout>/.deploy/.env)"` — or tell them its full path.
Tell them what goes where:

- `GEMINI_API_KEY` — a free key from <https://aistudio.google.com/apikey>,
  **Create API key**, pasted after the `=`. Without any key, expenses can still be
  entered by hand, but receipts are not sorted.
- `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `ZAI_API_KEY` — optional and free. Each adds
  models that take over when Gemini is busy; uncomment the line to use one.
- `OPENAI_API_KEY` — optional and paid, and only for the analysis dashboard on their
  computer; the server does not use it.
- `DINARY_SHEET_LOGGING_SPREADSHEET` — optional: a Google Sheet that gets a row for
  every expense. It needs a Google Cloud service account; skip it unless the user
  asks, and then go through `docs/src/en/google-sheets-setup.md` with them before
  step 7.

When they say it is saved, check that the key is filled, without printing it:

```bash
grep -cE '^GEMINI_API_KEY=.+' .deploy/.env
```

The count must be 1. Tell the user the keys stay in this file on their computer and
are copied to the VM by every deploy.

## 7. Set up and deploy

```bash
uv run inv setup-server
uv run inv deploy --ref=main
```

`setup-server` runs once: it installs the system packages, Node, uv, swap and
fail2ban, clones the app and starts it, and joins the VM to Tailscale. That last part
prints a login link (`To authenticate, visit: …`) and waits until it is used. Run
`setup-server` in the background and read its output as it goes — a foreground call
shows you nothing until it ends — and give the user the link to open and approve.
Start `deploy` only after `setup-server` has ended — moving, after the database
swap below. `deploy` builds the app on the VM, restarts it and waits up to 60
seconds for it to answer; run it the same way. Both take a long time on this small
VM; tell the user what is happening. Both are safe to repeat.

- **Moving from an old VM** with `.deploy/old-vm/dinary.db`: its first start
  created an empty database. Replace it between the two commands; `deploy` starts
  the app again and brings the database up to date:

    ```bash
    ssh ubuntu@<IP> 'sudo systemctl stop dinary && rm -f /home/ubuntu/dinary/data/dinary.db /home/ubuntu/dinary/data/dinary.db-wal /home/ubuntu/dinary/data/dinary.db-shm'
    scp .deploy/old-vm/dinary.db ubuntu@<IP>:/home/ubuntu/dinary/data/dinary.db
    ```

- If `setup-server` prints a Tailscale link about enabling Serve or HTTPS, the
  certificates from step 5 are off. Give the user that link; if `setup-server` has
  already ended, publish the app once they have approved:
  `ssh ubuntu@<IP> 'tailscale serve --bg 8000'`.
- If `apt` reports `Could not get lock`, the new VM is installing its own updates:
  wait five minutes and repeat the command.
- If `deploy` fails, read its output and `uv run inv logs --prod`, and explain to
  the user what went wrong. Never edit files on the VM.

Then read the name the VM got: `ssh ubuntu@<IP> tailscale status --json`, the first
label of `Self.DNSName`. It should be `dinary`, or, moving under the old name, that
name. Moving under a name of its own (step 4, item 1), `dinary-1` is expected: leave
the old machine alone. Otherwise, if the name is not the one wanted — Tailscale
appends `-1` when the name is taken — have the user remove any old machine still
listed under it, then rename the new one on
<https://login.tailscale.com/admin/machines>: the menu at the right of its row,
**Edit machine name**. A rename leaves the app published under the previous name, so
re-publish it: `ssh ubuntu@<IP> 'tailscale serve reset && tailscale serve --bg 8000'`.

On the same page, the same menu, the user chooses **Disable key expiry**. Otherwise
the VM leaves their network after 180 days and the app stops answering.

## 8. Check that it works

1. `uv run inv status --prod` shows the service active, and `tailscale serve status`
   in its output shows the HTTPS address proxying to `http://127.0.0.1:8000`. Its
   Litestream lines report a replication that is not set up yet; step 10 deals with
   it.
2. Read the app's address: `ssh ubuntu@<IP> tailscale status --json` gives it as
   `Self.DNSName`, without the trailing dot.
3. `ssh ubuntu@<IP> curl -fsS https://<address>/api/health` answers. That proves the
   private address and its certificate work. The first request can take several
   seconds while the certificate is issued.
4. `uv run inv verify-db --prod` reports no problems.
5. Moving from an old VM: the new database holds the same number of expenses as the
   copy from step 4 —
   `ssh ubuntu@<IP> "sqlite3 /home/ubuntu/dinary/data/dinary.db 'select count(*) from expenses'"`.

Report anything else to the user.

## 9. The phone — the user's steps

Moving from an old VM under the same name, the app already on their phone keeps
working: they open it and enter an expense. If it opens blank, they close it fully
and open it again — it still holds the old server's pages for one load. Under a new
name it is a new address: the old icon no longer works, so walk them through the
steps below.

Otherwise walk them through it one step at a time:

1. The Tailscale app is switched on, signed in with the same account, and lists the
   VM among its machines.
2. Open `https://<address>/` in Safari on an iPhone, or in Chrome on Android. Give
   them the exact address, and suggest sending it to the phone the way they usually
   do — a message to themselves, a note — so it can be tapped rather than typed.
3. Put it on the home screen. iPhone: **Share** (in recent iOS, under the **⋯**
   button), then **Add to Home Screen**. Android: the **⋮** menu, then **Add to
   Home screen** or **Install app**.
4. Open it from the home screen. On a first install it asks which set of categories
   to start with. Then they enter an expense, and scan a receipt's QR code to see
   its items sorted.

If the phone cannot open the address, check in this order: Tailscale is on in the
phone's app, the phone and the VM are in the same account, and step 8 passed.

## 10. Finish

Tell the user, briefly:

- what now runs where, and the app's address
- that their keys live in `<checkout>/.deploy/.env`
- that their expenses live only in the database on the VM. Every `inv deploy` first
  saves a copy on their computer, under `<checkout>/data/backups/`, but nothing does
  between deploys. dinary can stream the database to a second VM and keep daily
  copies on Yandex.Disk, as `docs/src/en/operations.md` describes. Offer it as a
  later task: it needs a second free VM joined to their Tailscale network, and
  `uv run inv setup-replica` asks for a Yandex.Disk app password, which the user
  types themselves in a terminal opened in `<checkout>`.

  Moving from an old VM that had it — `DINARY_REPLICA_HOST` is set in the keys file
  — set it up again now, without asking: until then the second VM keeps the
  pre-move copy, uploads it to Yandex.Disk every day, and its freshness check still
  reports it as fine. Run `uv run inv setup-replica`, then `uv run inv
  replica-resync` (the replica holds the old VM's history, which the new one cannot
  continue), then `uv run inv status --prod`: Litestream must be active and list the
  database.
- that `uv sync --inexact --group analytics`, then `uv run inv analytics`, in
  `<checkout>` opens an analysis dashboard with an AI chat on their computer, if
  they want it
- how to update later: paste the same prompt into an agent again and ask it to
  update; these instructions cover it
- moving from an old VM that still exists: it can be terminated in Oracle's console
  once they are satisfied — unless something else runs on it. `.deploy/old-vm/` can
  then be deleted.
