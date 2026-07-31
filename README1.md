# Nifty Large-Cap Dashboard

A free, local Streamlit dashboard for tracking Nifty 100 (large-cap) stocks using
Yahoo Finance data. Runs entirely on your machine — no API keys, no subscriptions.

## Features

- **Auto-updating symbol universe**: pulls the official Nifty 100 constituent list
  from NSE and caches it. Re-checks NSE every 90 days (quarterly index review) so
  additions/removals show up automatically — you never have to edit a stock list by hand.
- **Ever-growing local price history**: instead of re-fetching a fixed rolling window
  every time (which would eventually "forget" old data), each stock's full history is
  cached locally in `price_cache/` and only ever grows — first run seeds it with
  everything Yahoo has, every run after that just appends the new days. This means
  reference levels from 5, 7, or 10+ years ago never silently disappear.
- **Key columns**: Price, Day Change %, 52W High/Low, % from 52W High, 200 EMA,
  % from 200 EMA, Volume, 20-Day Avg Volume, Relative Volume.
- **Days Above/Below 200 EMA**: how many consecutive trading days the stock has
  been on its current side of the 200 EMA (e.g. "182d Above" or "14d Below").
- **Retest Level (X) + % From X**: the app scans the FULL local price history for
  every completed streak where the stock stayed above its 200 EMA for at least N
  days (configurable, default 200) and then finally closed back below the 200 EMA.
  The main table shows the **most recent** such streak's high as `X` — a classic
  level to watch for a "buy on retest." Cells within your chosen retest band are
  highlighted blue.
- **"Days Above EMA Before X"**: how mature the trend already was when it made
  that high — e.g. "220d" means the stock had already been above its 200 EMA
  for 220 days before printing X. ("Total Streak Days," visible in the
  Reference Level Ledger and per-stock caption, is the full length of that
  uptrend from start to finish.)
- **"Max Correction From X" and "Days for EMA to Reclaim X"**: for every
  streak, how deep price corrected from X (the maximum drawdown, from
  streak-end through either the reclaim date or "now" if it hasn't reclaimed
  yet), and how many days it took the 200 EMA to climb back and reclaim X
  (blank/"—" if it hasn't reclaimed yet — both figures are still provisional
  and updating in that case). These two together tell you how sharp and how
  prolonged the correction was — useful context for calibrating a stop-loss
  or a realistic target if a similar move plays out around a future retest.
  Shown in the per-stock caption and the Reference Level Ledger table.
- **"Retest Drawdown %" and "Retest Recovery Days"** (🔵 Reclaimed & retested
  rows only): a *separate* metric from the two above — this measures the
  RETEST itself, not the original pre-reclaim correction. Once price comes
  back down to retest X as support, how much FURTHER did it dip below X
  before recovering back above it, and how many days did that take? A retest
  that holds cleanly right at X shows 0%; a retest that overshoots and
  briefly breaks below X before bouncing shows the actual depth of that
  overshoot. Same idea, applied to the 5-Leg, Monthly Pivot S1, and Monthly
  S1 Shift Up scanners below (see their sections for specifics).
- **Every historical streak is tracked, not just the latest one, and classified
  into FOUR states** (not a simple naked/tested binary — see below for why).
  A stock can have several completed streaks over its lifetime, each with its
  own reference level X:
  - ⚪ **Naked** — price has never come back near X since the breakdown.
  - 🟠 **Testing resistance** — price came back near X from below, but the
    200 EMA hasn't reclaimed X yet. Outcome undetermined, higher risk.
  - 🟢 **Reclaimed, pending retest** — the 200 EMA has climbed back above X
    (only possible after price already spent a long stretch trading above X),
    but price hasn't yet pulled back down near X since reclaiming it. This is
    the live **watchlist** state: structurally bullish, but the actual
    low-risk pullback entry hasn't fired yet.
  - 🔵 **Reclaimed & retested** — EMA has reclaimed X and price has already
    pulled back near X as support at least once. That entry already played out.

  This four-way split matters because a simple "was it ever approached"
  binary conflates two very different situations: a stock testing X from
  below with the trend still unconfirmed (real risk of rejection), versus a
  stock that already broke out above X, proved it structurally with a
  reclaimed 200 EMA, and simply hasn't pulled back yet (the setup you're
  waiting for). All of this is recorded in a local SQLite ledger
  (`levels_ledger.db`) that persists across sessions and is fully
  recomputed (self-correcting) from the local price cache every run.
- **📚 Reference Level Ledger section** (bottom of the page): browse every
  level across the whole universe, filterable by status (naked / testing
  resistance / reclaimed-pending-retest / reclaimed-retested / all), symbol,
  and how close it currently is to price. This is where levels from
  years-old streaks keep surfacing, even long after they'd fall out of any
  fixed lookback window.
- **Chart**: click any row to see a candlestick chart (50/200 EMA overlay) and
  volume chart below. The chart plots the latest X level and any other
  still-unresolved older levels for that stock (color-coded by status), so
  you can see all live reference points at once. Already-resolved
  (reclaimed & retested) levels are left off the chart to reduce clutter.
  Adjustable period (6mo/1y/2y/5y/10y/max — "max" uses the full local cache).
- **Quick filters**: near 52W high/low, above/below 200EMA, volume spikes,
  retest-of-X setups, search box, a checkbox to show only stocks currently
  sitting inside their retest band, and a dropdown to filter the main table
  by the latest streak's 4-state status.
- **🔀 5-Leg EMA Reversal Pattern scanner** (bottom of page): a separate
  pattern detector on daily 20/50/200 EMA. Looks for a down-up-down-up-down
  structure where each down leg makes a new low vs. the down leg two
  positions back, and each up leg makes a *lower* high vs. the up leg two
  positions back — a classic multi-leg basing/distribution structure.
  Confirmed by: (a) price itself beating the reference leg's extreme (the
  primary, authoritative signal), OR (b) **both** the 20 and 50 EMA of the
  new leg clearing the reference leg's floor/ceiling — checked via the
  naturally dominant line of the pair (the 50 EMA for down legs, the 20 EMA
  for up legs), since if the slower/farther line clears it, the faster one
  necessarily already has. Path (b) only counts if price doesn't **clearly
  contradict** it (price isn't more than ~2% worse than the reference) —
  without that guard, a razor-thin EMA reading (often just smoothing lag)
  could validate a leg even when price obviously went the other way (e.g.
  EMA edges a fraction of a percent lower while price is actually 8-9%
  *higher* — a clearly higher low, not a lower one). Once 5 legs qualify,
  the episode is flagged with one of six statuses (the two "complete"
  branches each fold in the X/Y retest state directly, rather than needing
  a separate column):
  - 🟡 **Pattern forming (below 200 EMA)** — the normal case: the decline has
    already dragged the 50 EMA below the 200 EMA, and we're waiting for it
    to cross back above (a golden-cross-style recovery signal). X/Y still
    updating.
  - 🟢 **Complete (golden cross), pending retest** — the golden cross
    happened, X/Y are locked in, and at least one of the two hasn't been
    revisited yet — the live watchlist state.
  - 🔵 **Complete (golden cross), retested** — both X and Y have already
    been revisited since the golden cross. That opportunity already played out.
  - 🟠 **Forming above 200 EMA (rare)** — the whole 5-leg structure played out
    without the 50 EMA ever dropping below the 200 EMA (a shallower pullback
    within a longer uptrend). No golden cross to wait for, so X/Y come
    directly from the pattern's own low instead — still updating since the
    qualifying down leg (leg 5) hasn't finished yet.
  - 🟢 **Complete (above 200 EMA), pending retest** — same rare case, but
    leg 5 has finished and at least one of X/Y hasn't been revisited yet.
  - 🔵 **Complete (above 200 EMA), retested** — same rare case, both X and Y
    already revisited.

  **Invalidation rule**: leg 2 must not rally back above the high made during
  the leg immediately before leg 1 started (the pre-existing streak high).
  If it does, this was never a corrective down-up-down-up-down structure -
  it's just the prior uptrend continuing to a new high - so that attempt is
  discarded and the scanner looks for the next valid leg 1 later on.

  Persisted in the same local ledger (`levels_ledger.db`, a separate table),
  filterable by status, symbol, and proximity to X/Y. X and Y are each
  tracked independently under the hood with the same naked/tested retest
  logic used elsewhere, but rather than showing that as two separate
  columns, it's folded straight into the Status label above — "pending
  retest" whenever either one is still naked, "retested" only once both
  have been revisited. **"Retest Drawdown %" / "Retest Recovery Days"**
  report whichever of X or Y had the deeper post-retest dip (shown with
  "On Level" indicating which one) — the worse-case, more conservative
  figure, kept as one compact pair of columns rather than four.

- **📐 Monthly Pivot S1 Setup scanner** (bottom of page): a third independent
  pattern detector, built on the daily 200 EMA vs. the monthly Standard
  Pivot S1 (computed from the prior completed calendar month's High/Low/
  Close, held flat through the current month — `P=(H+L+C)/3`, `S1=2P−H`).
  - **Qualify**: S1 must stay above the 200 EMA for a minimum number of
    calendar months (default 2, configurable) with no touch of either level
    by price (a wick or a close-through both count as a touch).
  - **Track X**: once qualified, the running high is tracked — S1 vs. the
    200 EMA no longer matters from here (S1 can even drop back below the
    200 EMA on its own without invalidating anything). The first time price
    touches S1, **X** is fixed at the running high reached so far.
  - **Track Y**: from there, the running low is tracked as **Y** while
    waiting for the 200 EMA to climb up and cross above X.
  - **Invalidation**: a touch of the 200 EMA anywhere from the qualifying
    window through to that crossing discards the whole episode.
  - **Complete**: once the 200 EMA crosses above X without that happening,
    X and Y are both locked in as buy-on-pullback levels. As with the 5-Leg
    scanner, the Status column folds in the combined retest state directly:
    🟢 **Complete, pending retest** (at least one of X/Y still naked) or
    🔵 **Complete, retested** (both already revisited) — with the same
    combined "Retest Drawdown % / Retest Recovery Days" (worse of X/Y)
    columns as the 5-Leg scanner.
  Scans full history, recording every qualifying episode (not just the
  latest), in its own ledger table — filterable by status, symbol, and
  proximity to X/Y, same as the other two pattern scanners.

- **🔺 Monthly S1 Shift Up Setup scanner** (bottom of page): a fourth
  independent pattern, also built on the monthly Standard Pivot S1. For
  every calendar month where price touches or closes at/below that month's
  S1 (a wick or a close-through both count), the scanner checks the
  FOLLOWING month's S1 (computed from that month's own High/Low/Close).
  Most of the time, a month that fell to S1 drags the next month's S1 down
  too — the common, uninteresting case. In the rare case where the
  following month's S1 is actually **higher**, that signals strong
  responsive buying pushed the range up despite the touch — the setup this
  scanner captures. **X** = that month's lowest low, tracked afterward as a
  buy-on-revisit level:
  - **⚪ Naked** — X has never been revisited since tracking started.
  - **🟢 Tested** — price ran up away from X, then genuinely came back down
    within the retest band.
  - **🔴 Failed** — price dropped below X by more than a configurable
    threshold (default 8%) at any point — support decisively broken.
  Also tracks the maximum % price ran up before the resolving event (or
  before "now" if still naked) and how many days it took, for context on
  how much room a similar setup might run before offering a pullback entry.
  Once tested or failed, **"Drawdown After Test/Fail" / "Days to Recover"**
  show how much further price dipped below X after that event before
  climbing back above it (and how long that took) — for a failed episode,
  this shows the true eventual depth of the breakdown, since the 8%
  threshold is just where "failed" gets triggered, not the final low.
  Scans full history, one independent episode per qualifying month, in its
  own ledger table.

- **📊 Multi-Chart Comparison**: select up to 6 stocks and view their daily
  candlestick charts (with 50/200 EMA overlay) side by side in a grid — 1,
  2, 4, or 6 at a time, independent of the single-stock deep-dive chart.
  Useful for eyeballing several setups from the ledgers above at once
  without clicking back and forth.

## Setup (one-time)

1. Install Python 3.9+ if you don't have it.
2. Open a terminal in this folder and install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Run it

```bash
streamlit run app.py
```

This starts a local web server and should auto-open your browser to
`http://localhost:8501`. If it doesn't open automatically, just paste that
URL into your browser.

Leave the terminal window open while you use the dashboard. Press `Ctrl+C`
in the terminal to stop it.

**Heads-up on the first run**: since each stock's local price cache needs to be
seeded with its full available history, the very first run will take longer
(potentially several minutes for ~100 stocks) as it fetches everything Yahoo has
for each one. Every run after that is much faster, since it only fetches the
handful of new trading days since the last run.

## Using the dashboard

- **Sidebar**:
  - "Force-refresh symbol list (NSE)" — manually re-check NSE for index changes
    (otherwise it auto-checks every 90 days).
  - "Force-refresh prices now" — clear the 15-minute price cache and refetch
    (this reuses the local price_cache/ history, it does not re-download
    everything from scratch).
  - Search box, quick-view filters (near high/low, above/below 200EMA, volume spikes,
    retest-of-X setups), and sort controls.
  - "Min days above 200 EMA to qualify as a trend (N)" — how long a streak must
    last before its peak counts as an X level (default 200 days).
  - "Retest zone: % band around X" — how close current price must be to X to
    count as a live retest setup (default ±5%). This same % is also used to
    decide whether a historical level counts as genuinely "tested."
  - "Only show stocks currently inside the retest band" — filters the main
    table down directly using the % band above.
  - "Retest / reclaim status filter" — narrow the main table to just one of
    the four states (naked / testing resistance / reclaimed-pending-retest /
    reclaimed-retested) for the latest streak.
  - "Min days per leg" — for the 5-Leg EMA Reversal scanner: filters out
    short-lived 20/50 EMA whipsaws so they don't count as separate legs
    (default 5 days).
  - "Min calendar months S1 must stay clean above 200 EMA" — for the
    Monthly Pivot S1 scanner: how long S1 must stay above the 200 EMA with
    no touch of either, before a setup counts as qualified (default 2).
  - "% below X that counts as 'failed'" — for the Monthly S1 Shift Up
    scanner: how far below X price must drop before the level is marked
    failed rather than still naked (default 8%).
- **Table**: click any row to select it — a candlestick chart with 50/200 EMA,
  the latest X level, any other naked older levels, and a volume chart appear
  below.
- **Reference Level Ledger** (bottom of page): a standalone, filterable table
  of every completed streak level ever recorded for the universe, independent
  of the main table's stock selection.
- **5-Leg EMA Reversal Pattern scanner** (further down): a standalone,
  filterable table of every detected 5-leg episode, its status
  (pattern forming / probe complete), and its X/Y levels vs. current price.
- **Monthly Pivot S1 Setup scanner** (further down still): a standalone,
  filterable table of every detected pivot-based episode, its status
  (tracking X / X fixed, pending cross / complete), its X/Y levels vs.
  current price, and whether each of X and Y has been retested since.
- **Monthly S1 Shift Up Setup scanner** (further down still): a standalone,
  filterable table of every detected S1-shift episode, its status
  (naked / tested / failed), X vs. current price, and how much price ran
  up before the resolving event.

## Example workflows — how to actually use this for decision-making

These are starting frameworks, not signals to act on blindly — always combine
with your own risk management (position sizing, stop-loss placement, and
checking the broader market/sector context). None of this is investment
advice; it's a description of how the dashboard's filters and columns map to
some common technical setups.

**1. Momentum continuation near 52-week highs, confirmed by sustained volume**
   - Sidebar → Quick view: *"Near 52W High (within 5%)"*.
   - Then look at `Rel Volume` for each candidate — a single day above 1.5x
     could just be noise (results, a block deal, index rebalancing). What
     you're really looking for is volume *staying* elevated for several
     consecutive sessions, which suggests sustained accumulation rather than
     a one-off spike. Click into the candlestick chart and check the volume
     panel for the last 5-10 days to confirm the pattern, not just today's bar.
   - This is a "strength is confirming strength" setup — you're paying up for
     already-visible momentum, so risk is typically managed with a tighter
     stop (e.g. below the most recent swing low or the 20-day average) rather
     than a wide one.

**2. The core "reclaimed, pending retest" watchlist (the main use case this
   dashboard was built for)**
   - Sidebar → *"Retest / reclaim status filter"* → **"🟢 Reclaimed, pending
     retest"**. This surfaces every stock where the 200 EMA has already
     climbed back above a prior streak-high X, but price hasn't yet pulled
     back down to retest it as support.
   - Sort by `% From X` ascending (closest to zero first) to see which
     candidates are *currently* approaching their level, rather than ones
     still far away.
   - A common way to frame the entry: watch for price to approach X, hold
     above it (or bounce from slightly below it — some prefer letting it dip
     a few % under X before reacting, since exact levels rarely hold to the
     rupee), and confirm with a bounce of a defined size (e.g. "up 5% off the
     low of the retest day/week") before entering, with a stop placed below
     the retest low or below X itself.
   - This is the lower-risk case *precisely because* the EMA reclaim already
     did the work of confirming the breakout held — you're not guessing
     whether the uptrend is real, only whether this specific pullback holds.
   - Check "Max Correction From X" and "Days for EMA to Reclaim X" for this
     level. A deep, prolonged correction (e.g. 30%+ over 400+ days) suggests
     this was a serious drawdown before the reclaim — if a retest ever fails,
     a similarly sharp move down is plausible, so size the position and set
     the stop with that history in mind rather than assuming a shallow pullback.
     A shallow, quick correction suggests the opposite — less drama either way.

**3. Second-retest confirmation (higher conviction than the first touch)**
   - The dashboard tracks whether a level has been retested *at all*
     (`🔵 Reclaimed & retested`), but doesn't currently count *how many times*
     — so this step is a manual chart-reading habit on top of the ledger.
   - Filter to `🔵 Reclaimed & retested`, open the chart (use "max" or "10y"
     period), and look at the price action around X since the first
     retest. If price already bounced off X once and is now coming back
     down to it a second time, many traders treat that as a stronger signal
     than the first touch — a level that's been "tested and held" more than
     once has more participants defending it.
   - If you find yourself using this often, this is a natural place to
     extend the ledger schema (e.g. a `retest_count` field) - see
     "Customizing" below.

**4. Old, deeply "reclaimed" levels — the ones everybody can see**
   - Scroll to the **Reference Level Ledger**, filter Status to
     `🟢 Reclaimed, pending retest` or `🔵 Reclaimed & retested`, and sort by
     `Age (yrs)` descending.
   - A level from a streak that ended 4-7+ years ago, where price has since
     spent years above it (EMA long since reclaimed), is a level that's had a
     very long time to become "obvious" on every long-term chart. The
     argument for weighting these more heavily: the more participants who can
     see and recognize a level, the more likely it is to act as a real
     supply/demand zone when retested (a partly self-fulfilling effect) -
     as opposed to a level from a streak that ended last quarter, which fewer
     people have had time to notice.
   - Cross-check the `% From X` column to see which of these aged levels are
     actually within reach of the current price right now, rather than
     scrolling through ones that are 40% away.

**5. Avoiding a resistance test with real rejection risk**
   - Filter to `🟠 Testing resistance`. These are stocks where price has come
     back up near a prior high, but the 200 EMA hasn't confirmed the move by
     climbing above it yet - meaning the breakout hasn't proven itself.
   - Compare `Days Above EMA Before X` and the total streak length (visible
     in the ledger) for context: a level that was made after a long, mature
     uptrend (e.g. 200+ days already above the EMA) reads differently than
     one made early in a fresh move - a late-cycle high has more "tired
     seller" supply sitting overhead than an early one.
   - This category is where you'd generally want *more* confirmation (a clean
     breakout close, ideally with volume) before entering, not less - or you
     simply watch and wait for it to either resolve into `🟢 Reclaimed` (bullish)
     or fail and roll back down (bearish).

**6. Sanity-checking an already-extended trend before chasing it**
   - For a stock already `Above` its 200 EMA for a very long stretch (high
     `Days Above/Below 200EMA`) and trading far above the EMA (`% From 200
     EMA` deeply positive), ask whether you're buying strength or buying
     exhaustion. There's no dashboard column that answers this for you, but
     combining `Trend Duration` + `% From 200 EMA` + where price sits versus
     its 52-week high gives you the context to judge whether a pullback
     toward the EMA might be more prudent than chasing here.

**7. The 5-Leg EMA Reversal pattern as an early-warning bottoming watchlist**
   - Scroll to the 5-Leg EMA Reversal Pattern scanner, filter Status to
     `🟡 Pattern forming (below 200 EMA)` — these are stocks that have already
     completed the full down-up-down-up-down structure and where the decline
     has dragged the 50 EMA below the 200 EMA, but the golden cross back above
     hasn't happened yet. Since X/Y are still provisional here (the low may
     not be in yet), treat these as "on my radar, not yet resolved."
   - Once a stock flips to `🟢 Complete (golden cross), pending retest`, its
     X/Y are locked in and at least one hasn't been revisited yet — this is
     the live watchlist state. Sort by `% From X` / `% From Y` to see which
     are currently closest to their support zone. A common way to frame the
     entry: watch for price to approach X or Y, look for a bounce of a
     defined size before committing, and place the stop below the lower of
     X/Y (or below the most recent swing low if price is basing well above
     both). Once it flips to `🔵 Complete (golden cross), retested`, both
     levels have already been revisited — that opportunity already played out.
   - Occasionally you'll see `🟠 Forming above 200 EMA (rare)` or its
     `🟢`/`🔵` complete counterparts — the rare case where the whole 5-leg
     structure played out without ever dragging the 50 EMA below the 200 EMA
     (a shallower pullback inside a stronger uptrend). There's no golden
     cross to wait for in this case, but treat the pending-retest / retested
     distinction exactly the same way — just note in your journal that this
     one skipped the death-cross → golden-cross round-trip entirely.
   - Cross-reference with the streak-based Reference Level Ledger for the
     same stock — if a `🟢 Reclaimed, pending retest` streak level (workflow 2)
     sits near the same zone as this pattern's X/Y, that's two independent
     signals agreeing on the same support area, which is generally a
     stronger case than either alone.

**8. Monthly Pivot S1 setup as a third independent confirmation**
   - Filter the Monthly Pivot S1 scanner to `🟢 Complete, pending retest` and
     sort by proximity to X or Y — this is the live watchlist state, where
     at least one of the two levels hasn't been revisited yet. Unlike the
     streak-based and 5-Leg setups, both levels here come from the SAME
     episode and were fixed at different points in time — X first (when
     price touched S1), then Y later (the low reached while waiting for the
     200 EMA to confirm) — so a pullback to either is a candidate entry,
     with the other level as a natural stop-loss/target reference point.
     Once both have been revisited, it flips to `🔵 Complete, retested`.
   - `🟠 X fixed, pending cross` is the analogous "earlier-stage watchlist"
     state: the higher-timeframe structure (S1 held above the 200 EMA
     cleanly for months) is already established, X is already known, but
     the 200 EMA hasn't confirmed by crossing above it yet — worth
     tracking, not yet a completed setup.
   - A level that's still showing "pending retest" after a long time
     (especially if it's Y, which is often further from current price) may
     be worth watching for a deeper pullback.
   - Cross-reference with the streak-based ledger and the 5-Leg scanner for
     the same stock, same as workflow 7 — agreement across independent
     detectors on the same price zone is a stronger signal than any one
     alone.

**9. Suggested weekly routine**
   - Refresh the data (or let the 15-minute cache do it automatically).
   - Check the summary strip for a quick pulse: advancers/decliners, count
     above 200 EMA, volume-spike count, retest-of-X count.
   - Run through workflows 2 and 4 above (reclaimed-pending-retest, sorted by
     proximity and age) as your primary "watchlist maintenance" step.
   - Spot-check anything flagged in workflow 5 (testing resistance) to see if
     it resolved one way or the other since last time.
   - Check the 5-Leg scanner (workflow 7) for anything newly flipped to
     `🟣 Probe complete`, or newly qualified as `🟡 Pattern forming`.
   - Check the Monthly Pivot S1 scanner (workflow 8) for anything newly
     flipped to `🟢 Complete`, or newly in `🟠 X fixed, pending cross`.
   - Add anything interesting to your own separate watchlist/journal - this
     dashboard is a screener and reference tool, not a portfolio tracker.

## Notes on data & limitations

- Yahoo Finance data can occasionally lag or have brief gaps; this is normal for
  the free tier and fine for personal research use.
- The symbol list uses NSE's Nifty 100 index (the standard "large cap" universe in
  Indian markets = Nifty 50 + Nifty Next 50). If NSE's site is temporarily
  unreachable, the app automatically falls back to the last successfully cached
  list, so the dashboard keeps working.
- All caching is local — a `symbols_cache.json` file, a `price_cache/` folder
  (one Parquet file per stock), and a `levels_ledger.db` SQLite file will appear
  in this project folder after the first run. Nothing is sent anywhere except
  requests to NSE and Yahoo Finance themselves.
- "Tested" vs "naked" status is recomputed fresh every run from the full local
  price history, so it's always self-correcting — the SQLite ledger is there for
  persistence/browsing, not as the source of truth.
- This tool is for personal research/education only, not investment advice.

## Performance

- **Network I/O, not compute, is the dominant cost of a cold "first load."**
  Each symbol needs its full daily price history from Yahoo Finance. Doing
  that one symbol at a time (one HTTP round-trip per stock) is the single
  biggest source of a slow first load on a large universe — for ~130 stocks,
  130 sequential requests can easily take several minutes on their own,
  regardless of how fast the pattern-detection code is. `price_cache.py`'s
  `bulk_refresh_histories()` fixes this: before the per-stock loop runs, it
  batches every symbol needing a fetch into a small number of multi-threaded
  `yf.download()` calls (one batch for symbols with no local cache yet,
  one for symbols just needing recent days appended), instead of looping
  one request at a time. The per-symbol `get_full_history()` calls that
  follow then just read the already-fresh local cache — no further network
  access for a symbol already refreshed this run. This is the change most
  likely to meaningfully cut down a multi-minute first load.
- **Pattern-detection compute** across all four scanners is vectorized with
  numpy rather than looping with pandas `.loc[]` scalar lookups (an ~27x
  speedup was measured for the Monthly Pivot S1 detector alone at realistic
  history lengths) — this matters, but is now a small fraction of total
  load time compared to the network cost above.
- **SQLite schema setup/migration** (the `CREATE TABLE IF NOT EXISTS` +
  `PRAGMA table_info` + conditional `ALTER TABLE` checks for all four ledger
  tables) used to re-run on every single call to `_connect()` — and that
  function is called once per pattern type per symbol (4 × ~130 = ~520
  times per full refresh). It now only runs once per process via a
  module-level flag; every call after the first just opens a connection
  and returns immediately. Verified this doesn't skip a needed migration by
  testing it against a simulated pre-existing database missing the newer
  columns — the one-time migration still runs correctly on the first call.
- **Everything after the first load within the cache TTL should be fast.**
  All three ledger tables are read from SQLite through cached wrapper
  functions (`load_streak_ledger`, `load_five_leg_ledger`,
  `load_pivot_ledger`, `load_s1_shift_ledger`) using the same TTL as the
  main price fetch — so filtering, sorting, or switching a status dropdown
  does NOT re-read the database or reprocess the full universe; it only
  re-filters an already-cached, already-small DataFrame. If a filter change
  still feels slow, watch whether the "Fetching latest prices from Yahoo
  Finance..." spinner reappears — if it does, `fetch_metrics`'s cache is
  being missed for some reason (e.g. the 15-minute TTL genuinely expired
  between interactions), not a problem with the filter itself.
- "Force-refresh prices now" clears the price-fetch cache AND all four
  ledger-read caches together, so a refresh can never show stale ledger
  data alongside freshly recomputed prices.

## Troubleshooting a specific stock's pattern detection

If a stock's status in the 5-Leg or Monthly Pivot S1 scanner doesn't match
what you see on a real chart, don't guess — use the diagnostic scripts to
see exactly what the algorithm computed, straight from your local price
cache, bypassing Streamlit and its caching entirely:

```bash
python diagnose_five_leg.py RELIANCE
python diagnose_five_leg.py RELIANCE 10          # override min_leg_days

python diagnose_monthly_pivot.py POWERGRID
python diagnose_monthly_pivot.py POWERGRID 3     # override min_qualify_months
```

`diagnose_five_leg.py` prints every detected leg (start/end dates,
direction, EMA/price extremes) and the resulting episodes, plus an explicit
invalidation-rule check for each one.

`diagnose_monthly_pivot.py` prints the monthly H/L/C and computed P/R1/S1
table (cross-check this against your charting tool's own pivot indicator
first — a mismatch here means a data or calculation issue), a day-by-day
detail table showing S1 vs. the 200 EMA and exactly which days touched
either level, the resulting episodes, and every historical date S1 crossed
above the 200 EMA (useful for spotting a candidate start the detector
should have picked up but didn't).

When something looks wrong, paste the relevant section of that output back
for troubleshooting — real numbers beat guessing from a screenshot every
time (this is exactly how the DLF and BAJAJFINSV false positives got found
and fixed).

## Customizing

- Change `REFRESH_DAYS` in `symbols_fetcher.py` if you want a different
  symbol-list refresh cadence.
- Change `DATA_CACHE_TTL` in `app.py` to fetch prices more/less frequently.
- Delete a stock's file in `price_cache/` (or the whole folder) to force a
  fresh full re-download of its history on the next run.
- The ledger (`levels_ledger.db`) syncs itself on every run — any streak or
  5-leg episode the current detection logic no longer produces for a symbol
  is automatically deleted, not just left stale. So if you update the
  detection logic (e.g. a new invalidation rule), just rerun the dashboard
  and the ledger will catch up on its own; you don't need to delete the file.
  Deleting `levels_ledger.db` entirely still works too, if you want a fully
  clean rebuild from scratch (it will be fully rebuilt from the price cache
  on the next run).
- Want Nifty 500 / Midcap / a custom watchlist instead? Swap the URL in
  `NSE_INDEX_CSV_URLS` inside `symbols_fetcher.py` for the corresponding
  NSE index CSV (e.g. `ind_nifty500list.csv`), or replace `get_symbols()`
  with your own static list.
- Want to track *how many times* a level has been retested (see workflow 3
  above), not just whether it has been? In `app.py`'s `_all_completed_streaks`,
  instead of stopping at the first retest event, you'd walk forward counting
  every time price returns within the band after moving away again, and add
  a `retest_count` field to the dict that gets passed to `levels_store.upsert_levels`
  (which just needs a matching column added to the `CREATE TABLE` statement
  in `levels_store.py`).
- **5-Leg EMA Reversal pattern assumptions worth knowing about, in
  `five_leg_pattern.py`:**
  - At qualification, the scanner checks whether the 50 EMA is below or above
    the 200 EMA to decide which of the two branches applies (normal:
    wait for a golden cross; rare: use the pattern's own low directly) —
    only starts checking *after* the pattern has fully qualified, so an
    early, incidental crossing during an earlier leg doesn't count.
  - Legs 6 onward are intentionally unconstrained (no requirement to keep
    making new extremes) per the pattern's definition — only legs 1–5 are
    validated.
  - If a candidate leg fails its extension check, the scanner restarts the
    search from that failed leg (if it's a down leg) or from the next down
    leg after it (if it's an up leg) — so overlapping/adjacent attempts are
    handled, but only one episode per stock is recorded per non-overlapping
    time window.
  - Leg 2 is checked against the high of whatever leg came immediately before
    leg 1 (if any) — rallying above it invalidates the whole attempt (see
    "Invalidation rule" above). If leg 1 happens to be the very first leg in
    the available price history, there's no prior leg to check against, so
    this rule is skipped for that one case only.
  - `CONTRADICTION_TOLERANCE_PCT` (default 2.0) in `five_leg_pattern.py`
    controls how much worse price is allowed to be before it "clearly
    contradicts" an EMA-only confirmation and blocks it. Lower it for a
    stricter scanner (fewer, more price-confirmed legs), raise it if you find
    genuinely valid legs getting rejected over minor price noise.
- **Monthly Pivot S1 pattern assumptions worth knowing about, in
  `monthly_pivot_pattern.py`:**
  - "Touch" is defined the same way throughout as `Low <= level` — this
    covers both an intraday wick through the level and a full close below
    it, since a close below always implies the low was too, matching how
    you described seeing both cases on real charts.
  - The qualifying window uses `pd.DateOffset(months=min_qualify_months)`
    from the candidate start date — a practical "N months later" cutoff
    rather than a strict calendar-month-boundary count. If you specifically
    want calendar-boundary-aligned months (e.g. always Jan 1 - Mar 1 rather
    than "2 months from whatever day it started"), that would need a small
    change to how `qualify_target` is computed.
  - After any invalidation (an early S1 touch during qualification, or a
    200 EMA touch at any later stage), the scanner resumes searching for a
    fresh candidate start on the very next day — so a stock can have several
    independent episodes over time if the S1-above-200EMA condition recurs.
  - X and Y are tracked using the running High/Low respectively (not Close),
    consistent with the convention used in the other two pattern scanners.
- **Monthly S1 Shift Up pattern assumptions worth knowing about, in
  `monthly_s1_shift_pattern.py`:**
  - Every calendar month is evaluated independently — there's no "advance
    past this episode" logic like the other scanners, since each month's S1
    is its own self-contained check. A stock can have many episodes over
    its history.
  - The "failed" and "tested" checks both use `Low <=` a threshold (touch
    counts, same convention as everywhere else). Whichever resolves
    chronologically FIRST wins; if both would trigger on the exact same
    day, "failed" takes priority as the more conservative read.
  - A genuine "tested" retest requires price to first clear
    `2 * retest_pct%` away from X (confirming a real move, not noise) before
    a subsequent pullback counts — same anti-false-positive guard used in
    the other retest-tracking code.
  - Tracking starts at the first trading day of the month AFTER the
    touch+shift is confirmed, since that's the earliest point this would be
    knowable in real time (the shift itself can only be confirmed once the
    touch month has fully closed).
