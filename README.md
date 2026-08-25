# apple-stock-alerts

Checks Apple's (unofficial) `fulfillment-messages` store-pickup API every 30
minutes for five specific Mac configurations near ZIP **94402**, and pushes a
notification to [ntfy.sh](https://ntfy.sh) the moment any store newly reports
one as available for pickup.

Runs entirely on GitHub Actions' free scheduled-workflow tier — no server to
host or keep alive.

## Watched configurations

| Product | Part / product code |
|---|---|
| Mac mini (M4 / 24GB / 512GB) | `MCYT4LL/A` |
| MacBook Air 15" Midnight (M5 / 24GB / 512GB) | `RO_MBA_M5_15_INCH_MIDNIGHT_BET_BES_ULT_2026` |
| MacBook Air 15" Silver (M5 / 24GB / 512GB) | `RO_MBA_M5_15_INCH_SILVER_BET_BES_ULT_2026` |
| MacBook Air 15" Starlight (M5 / 24GB / 512GB) | `RO_MBA_M5_15_INCH_STARLIGHT_BET_BES_ULT_2026` |
| MacBook Air 15" Sky Blue (M5 / 24GB / 512GB) | `RO_MBA_M5_15_INCH_SKY_BLUE_BET_BES_ULT_2026` |

The MacBook Air entries are build-to-order configurations (24GB RAM is not a
fixed retail SKU on the 512GB tier), so they're queried with Apple's internal
product code plus the matching set of BTO `option.0` codes rather than a
plain `MDVxLL/A` SKU. See `check_stock.py` for the full option lists.

## Setup

1. **Create the repo.** Push this folder to a new repository, e.g.
   `github.com/hyungoos/apple-stock-alerts` (public or private — either
   works, this doesn't need any secrets).

   ```bash
   cd apple-stock-alerts
   git init
   git add .
   git commit -m "Initial commit: Apple stock alert bot"
   git branch -M main
   git remote add origin https://github.com/hyungoos/apple-stock-alerts.git
   git push -u origin main
   ```

2. **Enable Actions.** On github.com, open the repo's **Actions** tab and
   click "I understand my workflows, go ahead and enable them" if prompted
   (new repos sometimes need this once).

3. **Check workflow permissions.** Under **Settings → Actions → General →
   Workflow permissions**, make sure "Read and write permissions" is
   selected. The workflow needs this to commit `state.json` back to the repo
   after each run (that's how it remembers what was already in stock, so it
   only notifies on *new* availability instead of every 30 minutes).

4. **Subscribe to notifications.** Install the [ntfy app](https://ntfy.sh/)
   (iOS/Android) or open https://ntfy.sh/apple-stock-94402-alerts-2 in a
   browser tab, and subscribe to the topic `apple-stock-94402-alerts-2`.
   Anyone who knows this exact topic name can subscribe to it too — ntfy
   topics are unauthenticated by default — so treat the name as a shared
   secret if that matters to you, or add ntfy auth if you want it locked
   down (see ntfy's docs).

5. **(Optional) Test it immediately.** Go to the Actions tab → "Check Apple
   Stock" → "Run workflow" to trigger a run by hand instead of waiting for
   the next 30-minute tick.

## Changing what it watches

- **ZIP code:** edit `STOCK_ZIP` in `.github/workflows/check-stock.yml`
  (currently `94402`).
- **ntfy topic:** edit `NTFY_URL` in the same file.
- **Products:** edit the `PRODUCTS` list in `check_stock.py`. For a standard
  (non-BTO) SKU you only need `part_number`; for a build-to-order combo
  you'll need Apple's internal product code and `option.0` codes, which can
  be captured from the "check pickup availability" network request on the
  relevant apple.com configurator page (see this repo's original request
  for how those were found).

## How "available" is detected

Each run calls the fulfillment-messages endpoint per product, reads
`pickupDisplay` for every nearby store, and compares it against the last
known value in `state.json`. A notification fires only on an
`unavailable → available` transition per store+product pair, so restocks
you already got notified about won't page you again every half hour — only
a fresh transition does (e.g. it sells out and comes back later).

## Caveats

- This uses an undocumented Apple endpoint that isn't a public API — Apple
  could change its shape or block automated traffic at any time without
  notice. If runs start failing, check the Action logs first.
- Build-to-order configurations are made-to-order; a "pickup available" hit
  for one of the MacBook Air entries typically means a scheduled pickup
  slot became available, not that a finished unit is already sitting on a
  shelf — check the `pickup_quote` text in the log for the actual wording.
- GitHub Actions' cron scheduler is best-effort and can run a few minutes
  late during high load; treat "every 30 minutes" as approximate.
