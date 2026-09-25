# Running Lead Discovery Radar on Windows

This guide takes a Windows PC from nothing installed to a review queue with real
businesses in it. Follow it top to bottom. Copy every command exactly as written; the only
values you change are the ones the guide tells you to change.

## Before you start

**This is a testing build, not a product install.** Read this part before anything else.

- It runs on **this one computer only**. Nothing is deployed anywhere, and nobody else can
  reach it: the app listens on `127.0.0.1`, which is this machine and nothing else.
- It is **not backed up off this machine.** It makes a database backup every night, but
  that backup is a file on the same disk. If the disk fails or you delete the folder, the
  data is gone. Section 8 shows how to copy a backup somewhere else.
- **Do not give it a public address.** Do not forward a router port to it, put it behind a
  tunnel (ngrok, Cloudflare Tunnel and so on) or change the `127.0.0.1` addresses. It was
  not built or hardened to face the internet.
- It uses **your own Google API keys**, so every search is billed to your Google Cloud
  account. Section 5 says what that costs and how the app keeps it small.
- It **never contacts a business.** It does not send e-mail, SMS or messages, and it does
  not phone anyone. It finds businesses through Google, looks at each one's public
  homepage, and puts what it found in front of a person. Nothing leaves the app unless a
  person approves it.

### How to read this guide

There are two kinds of command window in this guide, and every command block says which
one it belongs in:

- **PowerShell** — Windows' own command window. Used only for a few Windows settings.
- **Ubuntu** — a Linux command window that runs inside Windows (section 2 installs it).
  Almost every command runs here.

A command block labelled `powershell` goes in PowerShell; one labelled `bash` goes in
Ubuntu. Copy the whole block, paste it with a right-click, and press **Enter**. Wait until
the command has finished (you get the prompt back) before you run the next one.

> **What has and has not been tested.** Every Ubuntu command in this guide was run, in
> order, from a fresh copy of the code, on a clean Ubuntu 24.04 system — the same system
> section 2 installs. It was **not** run on a freshly set-up Windows PC. The places where
> Windows itself is involved (installing WSL2 and Docker Desktop, Windows settings,
> PowerShell commands, the browser on Windows) are marked **Not yet tested on a fresh
> Windows PC** — expect friction there first, and tell us what you hit.

## 1. Prerequisites

### Your PC

- **Windows 11, or Windows 10 version 22H2**, 64-bit.
- **16 GB of RAM** is comfortable. 8 GB works if you follow the memory setting in
  section 1.4; the first build will be slow and the rest of the PC sluggish while it runs.
- **30 GB of free disk space** on drive C:. The app itself takes about 5 GB once built;
  the rest is room for Docker's build cache, the database and backups.
- An internet connection. The first build downloads roughly 2 GB.
- **Virtualisation switched on** in the PC's firmware (BIOS/UEFI). Most PCs sold since
  2018 have it on. Section 1.3 says how to check.

### The software

| What | Why | Version this guide was checked against |
|---|---|---|
| **Windows Subsystem for Linux (WSL2)** | Runs a real Ubuntu Linux inside Windows. Docker Desktop needs it anyway. | WSL 2.6.1 |
| **Ubuntu 24.04 LTS** (in WSL2) | The command window every step runs in | Ubuntu 24.04 |
| **Docker Desktop for Windows**, WSL2 backend | Runs the app's five parts (web page, API, background worker, database, queue) in containers | Docker Desktop 4.55.0 (Engine 29.1.3, Compose 2.40.3) |
| **Git** | Downloads the code. Installed *inside Ubuntu* in section 2; you do not need Git for Windows. | Git 2.43 (Ubuntu's) |
| **Make** | Every command in this project is a short `make ...` command. Installed inside Ubuntu in section 2. | GNU Make 4.3 |

These are the versions on the machine this project is built and run on (Windows 11 Pro,
build 26200). Newer versions should work. **Docker Desktop older than 4.27 will not
work**: it ships Docker Compose older than 2.24, which cannot read the app's configuration
file.

### 1.1 Why Ubuntu, and not plain Windows

Windows does not have `make`, which every command in this project uses. Rather than give
you a second, Windows-only set of commands that would drift away from the ones we use
every day, this guide runs everything inside Ubuntu on WSL2. Inside Ubuntu the commands are
exactly the ones we run ourselves. Docker Desktop already installs WSL2 behind the scenes,
so most of the work is done for you.

### 1.2 Install Docker Desktop

> **Not yet tested on a fresh Windows PC.** Docker Desktop was already installed on the
> machine this guide was checked on.

1. Download Docker Desktop from <https://www.docker.com/products/docker-desktop/> and run
   the installer.
2. When it asks, keep **"Use WSL 2 instead of Hyper-V"** ticked.
3. Restart the PC when it asks. Open **Docker Desktop** from the Start menu and accept the
   terms. You can skip the sign-in.
4. Leave Docker Desktop running whenever you use the app. Its whale icon sits in the
   system tray, next to the clock.

### 1.3 If Docker Desktop says virtualisation is off

If Docker Desktop says **"Virtualization support not detected"** or **"WSL 2 installation
is incomplete"**, the processor's virtualisation feature is switched off in the firmware:

1. Restart the PC and enter the BIOS/UEFI setup. The key is usually **F2**, **F10**,
   **Del** or **Esc**, shown briefly on the start-up screen. On Windows 11 you can also go
   *Settings → System → Recovery → Advanced start-up → Restart now → Troubleshoot →
   Advanced options → UEFI Firmware Settings*.
2. Find **Intel Virtualization Technology (VT-x)**, **SVM Mode** or **AMD-V** (often under
   *Advanced*, *CPU Configuration* or *Security*), set it to **Enabled**, save and exit
   (usually **F10**).
3. Back in Windows, open Task Manager → *Performance* → *CPU*. It should say
   **Virtualisation: Enabled**. Start Docker Desktop again.

On a work laptop the firmware may be locked; your IT department has to enable it.

### 1.4 Give WSL2 enough memory and disk

WSL2 takes **half of your PC's memory** by default. On a 16 GB PC that is 8 GB, which is
enough. On an **8 GB PC** it is 4 GB, which is too little for the first build; give it
6 GB plus swap space.

> **Not yet tested on a fresh Windows PC.** The machine this guide was checked on has
> 32 GB and no `.wslconfig`, so it ran on the default (16 GB).

To change it, open **Notepad**, paste the lines below, and save the file as
`%UserProfile%\.wslconfig` (for example `C:\Users\YourName\.wslconfig`). In the *Save as*
dialog, set *Save as type* to **All files** so Notepad does not add `.txt`.

```ini
[wsl2]
# On an 8 GB PC. On a 16 GB PC you do not need this file at all.
memory=6GB
swap=8GB
```

Then restart WSL so it reads the file. In **PowerShell**:

```powershell
wsl --shutdown
```

Wait ten seconds, then start Docker Desktop again from the Start menu.

**Disk.** Docker Desktop keeps everything — the app, its build cache and its database — in
one virtual disk on drive C:. You set its maximum size in Docker Desktop → *Settings* →
*Resources* → *Advanced* → **Disk usage limit**. Anything of **40 GB or more** is enough for
this app. The virtual disk grows as needed up to that limit, so what matters is that drive
C: has the free space for it to grow into (see *Your PC* above).

## 2. Install Ubuntu and connect it to Docker

> **Not yet tested on a fresh Windows PC.** On the machine this guide was checked on,
> Ubuntu was already installed; the Ubuntu commands in this section were run on a clean
> Ubuntu 24.04.

**Install Ubuntu.** Right-click the Start button → **Terminal (Admin)** (on Windows 10:
**Windows PowerShell (Admin)**), and run:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart the PC when it finishes. Ubuntu then opens by itself (if it does not, open
**Ubuntu 24.04** from the Start menu) and asks you to choose a **username and password**.
They are only for Ubuntu; they do not need to match your Windows login. When you type the
password nothing appears on screen; that is normal. Remember it: Ubuntu asks for it
whenever a command starts with `sudo`.

**Connect Docker Desktop to Ubuntu.** In Docker Desktop, open *Settings* (the gear icon) →
*Resources* → *WSL integration*, switch on **Ubuntu-24.04**, and click *Apply & restart*.

**Install the tools the commands need.** In the **Ubuntu** window:

```bash
sudo apt update && sudo apt install -y make git openssl nano
```

It asks for your Ubuntu password, then prints a lot of text. It is finished when you get
the prompt back (a line ending in `$`).

Check that Ubuntu can see Docker. This prints a version number:

```bash
docker compose version
```

Success looks like `Docker Compose version v2.40.3-desktop.1` (your number may be higher).
If it says `docker: command not found` or `The command 'docker' could not be found in this
WSL 2 distro`, the WSL integration switch above is not on — see *Troubleshooting*.

## 3. Line endings (why this guide keeps the code inside Ubuntu)

Windows and Linux end lines of text differently: Windows with two characters (CRLF),
Linux with one (LF). Git for Windows converts files to the Windows style when it downloads
them, and the app's scripts, its `Makefile` and its settings files break when they run
inside the Linux containers with Windows line endings.

You do not have to do anything about this if you follow the guide as written, for two
reasons:

1. The code is downloaded **by Git inside Ubuntu** (section 4), which never converts line
   endings.
2. The project ships a `.gitattributes` file that tells every Git — including Git for
   Windows — to keep this project's files in the Linux style.

What would still cause trouble is downloading the code with **Git for Windows** or
**GitHub Desktop**, or putting it in a Windows folder (anything under `C:\` or `/mnt/c/`).
Don't. Keep it in your Ubuntu home folder, as section 4 does. Section 4 ends with a check
that proves the files arrived in the right format.

## 4. Getting the code

In the **Ubuntu** window:

```bash
cd ~
git clone https://github.com/Taimoor-Kamran/Lead_Discovery_Rader.git lead-discovery-radar
cd lead-discovery-radar
git checkout v0.11.2
```

`git checkout v0.11.2` picks the exact version this guide was written for. It prints a
note about a *"detached HEAD"*; that is expected and harmless. If it says
`pathspec 'v0.11.2' did not match`, we have not published that version yet: tell us, and
do not continue on a different version.

Check the line endings. This prints `0`:

```bash
git ls-files --eol | grep -c 'w/crlf'
```

Every command from here on is typed in the **Ubuntu** window, **inside this folder**. If
you close the window, open **Ubuntu 24.04** again and run this first:

```bash
cd ~/lead-discovery-radar
```

## 5. The keys you need to get yourself

The app talks to up to three outside services, each with its own key. You create the keys
in your own accounts, so the usage and the bills are yours and visible to you. **Treat each
key like a password**: do not e-mail it, paste it into a chat or put it in a screenshot.

> **Not yet tested on a fresh Windows PC.** The Google Cloud and OpenAI consoles are
> websites and do not depend on Windows, but their menus change; if a name below does not
> match what you see, search the console for the API's name.

### 5.1 Google Places API (New) — required, costs money

**What it does.** Finds the businesses: name, address, phone number, website, rating and
number of reviews, for one industry in one city.

**Where to get it.**

1. Go to <https://console.cloud.google.com/> and sign in with your Google account.
2. **Create a project:** the project picker at the top → *New project* → name it
   `Lead Discovery Radar` → *Create*. Make sure the new project is selected in the picker.
3. **Turn on billing:** ☰ menu → *Billing* → link a billing account (add a card if you
   have none). Places does not work without billing, even inside Google's free allowance.
4. **Set a budget alert before anything else:** *Billing* → *Budgets & alerts* → *Create
   budget* → for example **$20 per month**, with the default e-mail alerts. This is what
   warns you if something goes wrong that nobody predicted.
5. **Enable the API:** ☰ menu → *APIs & Services* → *Library* → search for
   **Places API (New)** → *Enable*. Pick *Places API (New)*, not the older *Places API*.
6. **Create the key:** *APIs & Services* → *Credentials* → *Create credentials* →
   *API key*. Open the new key, name it `radar-places`, and under *API restrictions*
   choose *Restrict key* and tick **only Places API (New)**. *Save*. Copy the key (it
   starts with `AIza`).

**What it costs.** Every search is paid. The app asks Places for these fields and nothing
else:

`id`, `displayName`, `formattedAddress`, `addressComponents`, `location`,
`nationalPhoneNumber`, `internationalPhoneNumber`, `websiteUri`, `businessStatus`,
`types`, `primaryType`, `rating`, `userRatingCount`, `attributions`

Google prices a request by the most expensive field in it. `websiteUri`,
`nationalPhoneNumber`, `rating` and `userRatingCount` are **Enterprise-tier** fields, so
every request the app makes is billed as a *Text Search Enterprise* request. Google gives
a free monthly allowance per tier and charges per 1,000 requests after that; the current
figures are on Google's
[Places pricing page](https://developers.google.com/maps/documentation/places/web-service/usage-and-billing).
One request returns up to 20 businesses, and pages often come back short, so a
60-business search usually takes **5 or 6 requests** and a 20-business search 1 or 2. The
app shows the expected number on the search form before you run anything.

**Without it.** Nothing can be found. The app starts and you can sign in, but a search
fails at its first stage (*Discovery*) because Google refuses a request with no key.

### 5.2 PageSpeed Insights API — required, free

**What it does.** Measures each business website's speed, accessibility and technical
quality, the same scores as Google's PageSpeed Insights page.

**Where to get it.** In the same Google Cloud project: *APIs & Services* → *Library* →
search for **PageSpeed Insights API** → *Enable*. Then *Credentials* → *Create
credentials* → *API key*, name it `radar-pagespeed`, restrict it to **only PageSpeed
Insights API**, *Save*, and copy it.

Two restricted keys instead of one means a leaked key can only be used for the one thing
it was made for.

**What it costs.** Nothing. Google allows **25,000 calls a day** per project. The app makes
one call per business website it audits. The key travels in a request header
(`X-Goog-Api-Key`), never in the web address, so it does not end up in logs.

**Without it.** Searches still run and every other audit check still works, but the
PageSpeed scores show as *unknown* for every business, with the reason recorded. They are
never shown as zero.

### 5.3 OpenAI — optional, leave it out to start

**What it does.** When switched on, a language model reads what the audit found on each
business and adds a short written summary and its own suggestions next to the rule-based
ones.

**Without it** — and this is how the app is shipped — the setting `AI_PROVIDER=disabled`
means: no model is called and nothing is spent on AI. Every lead is still found, audited,
scored and explained by fixed, visible rules; each suggestion carries the badge **Rules**,
and the screens show no AI summary boxes. The *Health* page says *AI is off*. This is a
complete, supported way to run the app, and the one we recommend for your evaluation: it
keeps the only moving costs to Google.

**If you want to try it later**, you need an OpenAI account at
<https://platform.openai.com/> with billing set up and **a monthly spend limit set in the
OpenAI dashboard first**. It is billed per use by OpenAI. Ask us for the model names and
prices to put in `.env.prod`; switching it on or off later loses no data.

## 6. Configuration

All settings live in one file, `.env.prod`, which you create from the template
`.env.prod.example`. **`.env.prod` holds your keys and passwords: never send it to anyone
or commit it.** Git is set up to ignore it.

### 6.1 Create the file and generate the secrets

The database password and the sign-in secret are long random values. These commands
create the file and write both into it for you. In **Ubuntu**:

```bash
cp .env.prod.example .env.prod
PGPW=$(openssl rand -hex 24)
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$PGPW|; s|radar:CHANGE-ME@|radar:$PGPW@|; s|^JWT_SECRET=.*|JWT_SECRET=$(openssl rand -hex 32)|" .env.prod
```

They print nothing when they work.

### 6.2 Fill in the values only you know

Open the file:

```bash
nano .env.prod
```

Move with the arrow keys. To find a line, press **Ctrl+W**, type its name, press
**Enter**. Type your value directly after the `=` — no spaces, no quotes. Save with
**Ctrl+O** then **Enter**; exit with **Ctrl+X**. (Use `nano`, not Notepad: Windows editors
can change the line endings, see section 3.)

| Line | What to put there |
|---|---|
| `ADMIN_EMAIL=` | Your own e-mail address. It is the account you sign in with, and no e-mail is ever sent to it. It must be an ordinary address: not `@example.com` (the app refuses to start with one), and not on a reserved ending such as `.local`, `.test`, `.invalid` or `.localhost` (creating the account fails, see *Troubleshooting*). |
| `GOOGLE_PLACES_API_KEY=` | The `radar-places` key from 5.1. |
| `PAGESPEED_API_KEY=` | The `radar-pagespeed` key from 5.2. |
| `BOT_CONTACT=` | Your website or e-mail address. When the app visits a business's homepage it identifies itself and includes this, so a site owner can reach a person. |

### 6.3 The values to leave exactly as they are

Every other line already has the right value for an evaluation. The ones worth knowing
about:

| Line | Shipped value | What it is |
|---|---|---|
| `ADMIN_PASSWORD=` | *(empty)* | Leave it empty. The app generates a strong password and shows it to you **once** (section 7). |
| `POSTGRES_USER=`, `POSTGRES_DB=` | `radar` | The database's user and name. The password was generated in 6.1. |
| `POSTGRES_PASSWORD=`, `DATABASE_URL=`, `JWT_SECRET=` | *(generated in 6.1)* | Do not edit. If you change the database password after the first start, the app can no longer open its own database (see *Troubleshooting*). |
| `PLACES_DAILY_CALL_CAP=` | `200` | **Leave it at 200.** See 6.4. |
| `PLACES_RUN_CALL_CAP_MULTIPLIER=` | `4` | A second guard: one search may make at most 4 × (businesses ÷ 20) Places requests, so 12 for a 60-business search. Leave it. |
| `PLACES_MAX_RESULTS_PER_JOB=` | `60` | The most businesses one search can ask for. |
| `PSI_DAILY_CALL_CAP=` | `200` | The same kind of daily guard for PageSpeed. PageSpeed is free, but 200 is enough for three 60-business searches a day. |
| `AI_PROVIDER=` | `disabled` | No AI; see 5.3. Leave every `AI_...` and `OPENAI_...` line as it is. |
| `CRM_DESTINATION=` | `csv` | Approved leads go to a spreadsheet you download from the *CRM* page. No account needed. |
| `TIMEZONE=` | `UTC` | The clock for the nightly backup (02:00) and clean-up (03:00). You may set your own zone, for example `America/Chicago`. |

### 6.4 `PLACES_DAILY_CALL_CAP` — the one setting that protects your bill

This is the most requests the app may send to Google Places in one day, counted in UTC
(so the day ends at midnight UTC, not your local midnight). When a search would go over it,
the app stops **before** sending the request, so nothing is charged, and the search is
marked failed with a message beginning `QuotaExceededError`.

**Leave it at its default of 200, and do not raise it.** It is not there because we expect
you to need 200 requests a day — a normal evaluation uses a few dozen. It is there because
software has bugs. While we were building this, a fault made searches send the same Places
request over and over: **500 real, billable requests in two days** before we found it, and
the daily cap was the only thing that stopped each run. That fault is fixed, and a
per-search limit now sits in front of the cap, but the cap is still the last line between
a bug and your bill.

If a search stops on the cap, that is the cap doing its job. The error message ends with
*"Raise the cap in the environment or wait for UTC midnight"*: **wait for midnight UTC**
and run the search again. If you keep hitting it, tell us — that is a bug worth reporting,
not a setting to change.

### 6.5 Check the file

This prints every line from 6.1 and 6.2 that is still empty. **It prints nothing when the
file is complete:**

```bash
grep -E '^(ADMIN_EMAIL|GOOGLE_PLACES_API_KEY|PAGESPEED_API_KEY|BOT_CONTACT|JWT_SECRET|POSTGRES_PASSWORD)=$' .env.prod
```

## 7. First run

Make sure Docker Desktop is running (whale icon in the tray). Then run these four steps in
order, in **Ubuntu**, inside `~/lead-discovery-radar`. Wait for each one to finish.

**Step 1 — build and start.** The first time takes around ten minutes; it downloads and
builds everything. Later starts take under a minute.

```bash
make prod-up
```

It prints a great deal while it builds. If your connection is slow you will see many lines
like `WARN  Tarball download average speed 13 KiB/s ... is below 50 KiB/s`; they are
harmless. Success: the last lines list the five parts, each ending in **Healthy** (the
order may differ), and you get the prompt back:

```text
 ✔ Container radar-prod-redis-1     Healthy
 ✔ Container radar-prod-postgres-1  Healthy
 ✔ Container radar-prod-api-1       Healthy
 ✔ Container radar-prod-worker-1    Healthy
 ✔ Container radar-prod-web-1       Healthy
```

**Step 2 — create the database tables and register the data sources.** Note the
`PROD=1`: without it, the command works on a different, empty development database (see
*Troubleshooting*).

```bash
make migrate PROD=1
```

Success: a list of `INFO ... Running upgrade ...` lines, then the data sources it
registered — **four** of them — and the prompt back:

```text
Synced 4 source(s):
  google_places        kind=api       enabled=True
  pagespeed_insights   kind=api       enabled=True
  openai               kind=api       enabled=True
  airtable             kind=api       enabled=True
```

If it says **5** sources and one of them is `demo_fixture`, the command went to the wrong
database: see *Troubleshooting* → *"`Synced 5 source(s)` with a `demo_fixture` line"*.

**Step 3 — create your administrator account.**

```bash
make seed-admin PROD=1
```

Success (among a few other lines):

```text
Admin you@yourcompany.com created (id 80dc2512-1927-45f5-8d92-4d239ff1e582).
Generated password: ...
This is shown ONCE and cannot be recovered. Save it now — if you lose it, use `make reset-password EMAIL=...`.
```

The id is a long random code and yours will differ.

**Copy that password somewhere safe now** (a password manager, or on paper). It is not
shown again. If you lose it, see *Stopping, restarting and starting over*.

**Step 4 — open the app.** In your Windows browser, go to:

**<http://127.0.0.1:3000>**

Type it exactly like that — `127.0.0.1`, not `localhost`, or signing in fails. Sign in
with your `ADMIN_EMAIL` and the generated password. The app asks you to choose your own
password straight away (at least 12 characters). You then land on the **Review queue**,
which says *Nothing to review.* — you have not searched for anything yet.

> **Not yet tested on a fresh Windows PC.** The browser part was checked with a Chromium
> browser on the same Ubuntu system, not with Edge or Chrome on Windows. Docker Desktop
> forwards `127.0.0.1:3000` from Ubuntu to Windows, so it should behave the same.

## 8. Your first twenty minutes

An empty review queue tells you nothing, so here is one small, complete run — one
industry, one city, twenty businesses — from search to CRM. It costs one or two Places
requests and up to twenty (free) PageSpeed calls.

> **Not yet tested from this guide.** This section needs real Google keys, and the
> walkthrough of this guide was done without spending on them. The screens and buttons
> named here are the ones the app's automated tests click through, and the product has
> been run this way with real keys on the builder's machine — but not step by step from
> this text. It is the first thing to check when you follow it.

**Before you start the search**, make sure the PC will not go to sleep for the next half
hour: see *Troubleshooting* → *"Audits failed after the PC slept"*. A sleeping PC drops its
network connection, and the audit stage fails for every business it was working on.

1. **Look at the baseline.** Open **Health** in the menu. Under *External API error rate
   (24 h)* it says *No API calls in the last 24 h.* This is where you will see the traffic
   the search generates.
2. **Set up the search.** Open **Searches**. Pick an **Industry** (for example
   *Plumbing*), keep **City and state**, and type a **City** and **State** (a US city,
   for example `Austin` and `TX`). Set **Max results** to `20` and click outside the box.
3. **Read the estimate** (*Cost estimate for 20 results*) under the form before you run
   anything. For twenty results it says **Google Places calls (at most) 4 — 200 of 200
   left today** and **PageSpeed calls (at most) 20**. The 4 is the per-search safety limit
   from 6.3, not a forecast: a twenty-result search normally makes 1 or 2 Places requests.
   *AI calls* says *AI is off — this run costs nothing beyond the calls above*. If **Save
   and run** is greyed out, hover over it: it says why (usually that the daily cap would
   not cover it).
4. **Run it.** Click **Save and run**. The search's page opens with four stages:
   **Discovery** (asking Google Places), **Resolution** (merging duplicates),
   **Audit** (visiting each homepage and measuring it with PageSpeed) and
   **Classification** (turning findings into suggestions). Discovery takes seconds. Audit
   takes the longest: about half a minute per business, so around ten minutes for twenty.
   The page updates by itself.
5. **Open the review queue.** When the stages are done, click **Open the review queue** on
   the search's page. Each row is one business, strongest first: its city, its suggested
   services with a score and a confidence, the audit status and its worst findings.
   Businesses where the evidence is weak are hidden until you switch on **Show weak
   signals**.
6. **Open one business and read it.** Click a row. On the left are the facts, each showing
   where it came from (*Source*). In the middle is the audit: each finding with its
   evidence and a link to the page it was found on, the PageSpeed scores, and the technology
   the site runs on. On the right are the suggested services (*Opportunities*): the reason,
   the evidence, the score broken into its parts, and a **Rules** badge saying the rules
   produced it. Check one finding against the business's real website — that is the
   judgement the app leaves to you.
7. **Approve it.** On an opportunity you agree with, click **Approve** (you may add a note
   and assign a sales rep; both are optional) and confirm. A message appears with
   **Undo**; you have 30 minutes to change your mind.
8. **See it reach the CRM.** Open **CRM**. The approval waits out its 30-minute undo window
   under **Scheduled**. To skip the wait for this test, click **Send now** on its row — this
   skips the waiting time, never the approval. The lead moves to **In CRM**. Click
   **Export CSV (new)**: a spreadsheet downloads to your Windows *Downloads* folder, with one
   row per approved business, ready for Excel.
9. **See the traffic.** Back on **Health**, *External API error rate (24 h)* now shows each
   service the search used and how many calls it made to it — `google_places` and
   `pagespeed_insights`. (The visits to each business's homepage are ordinary web page
   loads, not API calls, and are not counted here.) Google's own
   count is in the Cloud console → *APIs & Services* → *Enabled APIs & services* → click
   the API → *Metrics*. Google's figures can take a few minutes to appear.

That is the whole loop: a search finds businesses, the app audits each one and explains
what it would sell them, a person decides, and only what the person approved leaves the
app.

## 9. Stopping, restarting and starting over

All of these run in **Ubuntu**, inside `~/lead-discovery-radar`.

| To… | Run |
|---|---|
| **Stop** the app. Your data is kept. | `make prod-down` |
| **Start** it again, or apply a change you made to `.env.prod` | `make prod-up` |
| See whether each part is running | `make prod-ps` |
| Watch what it is doing (press **Ctrl+C** to stop watching; the app keeps running, and the `make: *** ... Error 130` line that follows is normal) | `make prod-logs` |
| Choose a new password if you lost yours | `make reset-password EMAIL=you@yourcompany.com PROD=1` |

In the reset command, use your own `ADMIN_EMAIL`. It asks for the new password twice.

Starting again only needs Docker Desktop running and `make prod-up`; you do not repeat
`make migrate` or `make seed-admin`. If the PC restarts while the app is running, the app
comes back by itself once Docker Desktop has started.

### Back up the database to a file

```bash
make backup PROD=1
```

Success: among some lines of log output, it prints the file it wrote:

```text
Backup written: /app/backups/radar-20260926-143000.dump (80714 bytes)
The dump holds the database only. Copy .env.prod somewhere safe separately.
```

`/app/backups` is the app's name for the `backups` folder inside `~/lead-discovery-radar`. The app also does this every night at 02:00 and keeps
the newest fourteen.

**Copy the backup off this machine** — that is what makes it a backup. To see the folder
in Windows Explorer:

```bash
explorer.exe backups
```

Copy the newest `.dump` file to a USB drive or cloud folder. Copy `.env.prod` as well
(`explorer.exe .` shows it; files starting with a dot may need *View → Show → Hidden
items*): **a backup is useless without it**, because it holds the passwords and keys the
database was set up with. Keep that copy as private as the keys themselves.

> **Not yet tested on a fresh Windows PC.** `explorer.exe` opening an Ubuntu folder is a
> WSL feature that was not exercised in the walkthrough.

To put a backup back (it replaces everything in the app with the backup's contents, and
asks you to type a confirmation first):

```bash
make restore FILE=backups/radar-20260926-143000.dump PROD=1
```

Use the name of your own file. It asks you to type `RESTORE` and the file name, stops
the app, restores, and ends with `Restored radar-....dump; api and worker are back up.`

### Wipe everything and start clean

This **deletes every search, business, review decision and user**, and cannot be undone.
Backup files in `backups/` are kept.

```bash
make prod-down ARGS=-v
make prod-up
make migrate PROD=1
make seed-admin PROD=1
```

`ARGS=-v` is what deletes the data; without it, `make prod-down` only stops the app. After
this, a new admin password is generated: copy it, as in section 7.

## 10. Troubleshooting

Find the entry by what you see.

### "Audits failed after the PC slept", or a search's *Audit* stage shows failed businesses after you left the PC

**Why.** When a Windows laptop goes to sleep (Modern Standby), Windows drops its network
connection, even though the app keeps running. Every call the app was in the middle of
fails. We confirmed this against the Windows event log on our own machine: twelve calls
failed between the PC going to sleep at 01:05:47 and waking at 03:08:50. An audit of
fifty businesses takes twenty-five to thirty minutes, and its time limit counts real time,
asleep or not, so a PC that sleeps partway through a run produces failed audits.

**Fix.** Keep the PC awake, plugged in and with the lid open, for as long as a search is
running. Before a run, in **PowerShell** (no admin needed):

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
```

This stops the PC sleeping and the screen switching off while it is plugged in. After the
run, set your usual values back — for example 30 minutes to sleep and 10 to screen off:

```powershell
powercfg /change standby-timeout-ac 30
powercfg /change monitor-timeout-ac 10
```

(You can do the same in *Settings → System → Power & battery → Screen, sleep & hibernate
timeouts* — set the *plugged in* values to *Never*.) Closing the lid usually sends a laptop
to sleep whatever these say, so keep it open. Then run the search again: a business whose
audit failed is audited again on the next run.

> **Not yet tested on a fresh Windows PC.** The sleep failure itself was observed on
> Windows 11; the `powercfg` commands above were not run as part of the walkthrough.

### "Bind for 127.0.0.1:3000 failed: port is already allocated" (or 8000)

**Why.** Another program already uses port 3000 (the web page) or 8000 (the API).

**Find the offender.** In **PowerShell**:

```powershell
Get-NetTCPConnection -LocalPort 3000,8000 -State Listen | Select-Object LocalAddress, LocalPort, OwningProcess, @{n='Program'; e={(Get-Process -Id $_.OwningProcess).ProcessName}}
```

- If *Program* is **`com.docker.backend`** or **`wslrelay`**, the port is held by Docker
  itself — usually a second copy of this app. See the next entry.
- Anything else (for example `node`), close that program, then run `make prod-up` again.

If you cannot close it, you can move the app to other ports. Open `.env.prod` with
`nano .env.prod`, change these five lines so they read as below (each one is already in
the file), save, and run `make prod-up`. The address then becomes
<http://127.0.0.1:3001>.

```text
WEB_PORT=3001
CORS_ORIGINS=http://127.0.0.1:3001
APP_BASE_URL=http://127.0.0.1:3001
API_PORT=8001
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8001/api/v1
```

### Two copies of the app: "port is already allocated" although nothing else is running

**Why.** This project has two ways of running: the one this guide uses (called
`radar-prod`) and a developer version (called `lead-discovery-radar`). They use the same
ports, so only one can run at a time. The developer version starts when a `make` command
is run **without** `PROD=1` (see the next entry).

**Check which is running.** In **Ubuntu**:

```bash
docker compose ls
```

It lists each running copy by name. If you see `lead-discovery-radar` in the list, stop it
(this does not touch your data):

```bash
make down
```

Then `make prod-up` again.

### `Synced 5 source(s)` with a `demo_fixture` line, `created .env from .env.example`, or `relation "users" does not exist`

**Why.** `make migrate` (or `make sync-sources`, `make seed-admin`, `make backup` …) was run
**without** `PROD=1`. Without it, the command does not touch your app at all: it quietly
creates a settings file called `.env`, starts a separate, empty developer database, and
does its work there. The tell-tale signs are the first line of its output,

```text
created .env from .env.example — set JWT_SECRET and ADMIN_EMAIL before using it for real
```

and a source list of **five**, including `demo_fixture`, where your app has **four**. It may
also spend a few minutes building something first. Your app is unchanged: if this was your
first `make migrate`, its database still has no tables, and `make seed-admin PROD=1` fails
with `relation "users" does not exist`.

**Fix.** Stop the developer copy it started (this does not touch your app or its data),
then run the command again with `PROD=1`:

```bash
make down
make migrate PROD=1
```

The `.env` file it created is harmless and can stay.

### `make prod-up` ends with `container radar-prod-worker-1 is unhealthy` (or `api`), and "Refusing to start in environment 'production'"

**Why.** Before starting, the app checks `.env.prod` and refuses an unsafe setting. See
the reasons with:

```bash
make prod-logs
```

(**Ctrl+C** to stop watching.) It lists every problem at once. The usual ones:
`ADMIN_EMAIL` is empty or ends in `@example.com`, or `JWT_SECRET` is too short because the
commands in 6.1 were skipped. Fix the lines it names, then `make prod-up` again.

### The sign-in page shows **API health — Status: degraded, Database: down**

The sign-in page has a small *API health* box under the form. If it says *Database: down*,
signing in fails with *"Couldn't reach the API"*, and `make prod-logs` shows
`password authentication failed for user "radar"`, then:

**Why.** The database was created with one password and `.env.prod` now has another —
usually because the commands in 6.1 were run a second time after the first start. The
database keeps the password it was created with. Note that `make prod-up` still reports
every part as **Healthy** in this state; the *API health* box is where it shows.

**Fix.** If you have a copy of the old `.env.prod`, put it back and run `make prod-up`.
Otherwise, and only if there is nothing in the app you need, wipe and start clean
(section 9) — and do not run 6.1 again afterwards.

### `ADMIN_EMAIL / ADMIN_PASSWORD are not usable: value is not a valid email address`

Shown by `make seed-admin PROD=1`, usually ending *"The part after the @-sign is a
special-use or reserved name that cannot be used with email."* The address in
`ADMIN_EMAIL` ends in a reserved name such as `.local`, `.test`, `.invalid` or
`.localhost` — common for internal company addresses. The app starts with it but cannot
create the account. Put an ordinary address in `ADMIN_EMAIL` (`nano .env.prod`), then run
`make seed-admin PROD=1` again. No restart is needed; nothing is ever e-mailed to it.

### Docker commands fail: "Cannot connect to the Docker daemon", "docker: command not found", or "The command 'docker' could not be found in this WSL 2 distro"

**Why.** Docker Desktop is not running, or it is not connected to Ubuntu.

**Fix.** Start **Docker Desktop** from the Start menu and wait until its window says
*Engine running*. If it still fails, open Docker Desktop → *Settings* → *Resources* →
*WSL integration*, switch on **Ubuntu-24.04**, click *Apply & restart*, then close the
Ubuntu window and open a new one.

### "WSL 2 is not installed", `wsl: command not found`, or Ubuntu is not in the Start menu

Run `wsl --install -d Ubuntu-24.04` in an **admin** terminal (section 2) and restart. If
Windows says the *Virtual Machine Platform* feature is missing, run
`wsl --install --no-distribution`, restart, then run `wsl --install -d Ubuntu-24.04`
again. If virtualisation is off, see section 1.3.

### The build stops partway, the PC becomes very slow, or you see `Killed`, `exit code 137` or `no space left on device`

**Why.** WSL2 ran out of memory or disk while building.

**Fix.** Give it more memory with the `.wslconfig` file in section 1.4, and check the
*Disk usage limit* there as well as the free space on drive C:. Then run `make prod-up`
again: parts already built are kept, so it continues where it stopped. If it fails again
on disk space, free Docker's build cache (this does not touch your data) and try once more:

```bash
docker builder prune -f
```

If it still fails, send us the last 30 lines of the output.

### `QuotaExceededError: The daily call cap for 'google_places' ... has been reached`

**Not a failure.** The daily safety cap from 6.4 stopped the request before it was sent,
and nothing was charged. The message says you can raise the cap; please don't. Wait until
midnight UTC and run the search again.

If the message names `pagespeed_insights` instead, it is the free PageSpeed cap. The
Places part of the search already ran; wait until midnight UTC and run it again, and the
audit picks up the businesses it skipped.

### "Safety limit reached: this run made … 'google_places' call(s)"

The per-search limit (`PLACES_RUN_CALL_CAP_MULTIPLIER`, see 6.3) stopped a search that was
making far more Places requests than its size needs; the next request was not sent. The
message says so itself: a normal run stays well under the limit, so reaching it means the
run was looping. That should never happen — please tell us which search it was. Do not
raise the limit.

### "Couldn't reach the API. Check that the api container is running (`make prod-ps`)." on the sign-in page

**First check the address bar.** If it says `localhost:3000`, that is the cause, and the
API is fine: the app only accepts `http://127.0.0.1:3000`. On `localhost` the *API health*
box under the form shows the same message. Close the tab and open
<http://127.0.0.1:3000>. (We saw exactly this message when signing in on `localhost`
while everything was healthy.)

If the address is `127.0.0.1:3000`, read the *API health* box: *Database: down* is the
entry above. Otherwise run `make prod-ps` and check that `api` says *healthy*; if it does
not, `make prod-logs` says why.

### Scripts fail with `$'\r': command not found`, `bad interpreter`, or `/usr/bin/env: 'bash\r'`

**Why.** The code was downloaded with Windows line endings — usually with Git for Windows,
or into a Windows folder (section 3).

**Fix.** Delete that copy and download it again inside Ubuntu, exactly as in section 4.
