"""
five_leg_pattern.py
---------------------
Detects the "5-leg EMA reversal" pattern using daily 20/50/200 EMA.
See app.py / README for the full narrative description.
"""
import pandas as pd
import numpy as np


def _debounce_below_series(below: pd.Series, min_leg_days: int, max_passes: int = 6) -> pd.Series:
    """Merges whipsaw runs shorter than min_leg_days into their neighboring leg."""
    s = below.copy()
    for _ in range(max_passes):
        groups = (s != s.shift()).cumsum()
        changed = False
        for gid in groups.unique():
            idx = groups[groups == gid].index
            if len(idx) < min_leg_days:
                pos = s.index.get_loc(idx[0])
                if pos > 0:
                    s.loc[idx] = s.iloc[pos - 1]
                    changed = True
                elif pos + len(idx) < len(s):
                    s.loc[idx] = s.iloc[pos + len(idx)]
                    changed = True
        if not changed:
            break
    return s


def _get_legs(hist: pd.DataFrame, ema20: pd.Series, ema50: pd.Series, min_leg_days: int):
    below = _debounce_below_series(ema20 < ema50, min_leg_days)
    groups = (below != below.shift()).cumsum()
    legs = []
    for gid in groups.unique():
        idx = groups[groups == gid].index
        direction = "down" if bool(below.loc[idx[0]]) else "up"
        e20 = ema20.loc[idx]
        e50 = ema50.loc[idx]
        if direction == "down":
            # 20 EMA <= 50 EMA throughout a down leg, so ema20 is the
            # naturally lower line. The leg's floor (used as the REFERENCE
            # value for later legs to clear) is the lower of the two.
            ema_extreme = min(e20.min(), e50.min())
            # For CANDIDATE comparisons: 50 EMA is the naturally higher line
            # in a down leg, so requiring it to also clear a prior floor is
            # the stricter 'both lines below' test (if the higher line
            # clears, the lower one necessarily already has).
            ema50_extreme = float(e50.min())
            ema20_extreme = float(e20.min())
            price_extreme = float(hist.loc[idx, "Low"].min())
        else:
            # 20 EMA >= 50 EMA throughout an up leg, so ema20 is the
            # naturally higher line - the leg's ceiling.
            ema_extreme = max(e20.max(), e50.max())
            ema50_extreme = float(e50.max())
            ema20_extreme = float(e20.max())
            price_extreme = float(hist.loc[idx, "High"].max())
        legs.append({
            "start": idx[0], "end": idx[-1], "direction": direction,
            "ema_extreme": float(ema_extreme),
            "ema20_extreme": ema20_extreme,
            "ema50_extreme": ema50_extreme,
            "price_extreme": price_extreme,
        })
    return legs


CONTRADICTION_TOLERANCE_PCT = 2.0


def _extends(candidate, reference):
    """
    A candidate leg 'extends' the reference leg (lower low for a down leg,
    lower high for an up leg) if EITHER of the following confirms it:

    1. Price test - price itself (Low for down legs, High for up legs) beats
       the reference leg's price extreme. This is the primary, authoritative
       signal - price is ground truth.
    2. EMA test - BOTH the 20 and 50 EMA of the candidate leg have cleared
       the reference leg's floor/ceiling (min or max of ITS 20 and 50 EMA),
       checked via the naturally DOMINANT line of the candidate (50 EMA for
       a down leg, 20 EMA for an up leg - since if that line clears, the
       other necessarily already has). This exists to cover the case the
       pattern was originally described with: price has made a genuine new
       extreme but the EMA pair hasn't quite caught up to confirm it yet.

       Critically, the EMA test is only allowed to validate the leg on its
       OWN if price does not CLEARLY contradict it - i.e. price is not more
       than CONTRADICTION_TOLERANCE_PCT worse than the reference's price
       extreme. Without this guard, a razor-thin EMA reading (often just
       smoothing lag, not a real move) could validate a leg even when price
       obviously went the other way - e.g. EMA edges a fraction of a percent
       lower while price is actually 8-9% HIGHER (a clearly higher low, not
       a lower one). In that situation the EMA reading is almost certainly
       noise, not a genuine confirmation, and should not carry the leg on
       its own.
    """
    if candidate["direction"] == "down":
        # A down leg "extends" if it makes a LOWER low than the reference down leg.
        # ema_extreme for a down leg = min(ema20, ema50) — the floor reached.
        # ema50_extreme is the dominant (higher) line in a down leg; if its floor
        # clears the reference floor, the lower ema20 necessarily already has.
        ema_test = candidate["ema50_extreme"] < reference["ema_extreme"]
        price_test = candidate["price_extreme"] < reference["price_extreme"]
        # Price clearly contradicts if it is materially HIGHER (did NOT make a new low).
        price_clearly_contradicts = candidate["price_extreme"] > reference["price_extreme"] * (
            1 + CONTRADICTION_TOLERANCE_PCT / 100.0
        )
    else:
        # An up leg "extends" if it makes a LOWER HIGH than the reference up leg.
        # This is a DECLINING 5-leg structure: counter-trend rallies (legs 2, 4)
        # must show progressive weakness — each up leg peaks lower than the last.
        # ema_extreme for an up leg = max(ema20, ema50) — the ceiling reached.
        # ema20_extreme is the dominant (higher) line in an up leg; checking its
        # ceiling is the strictest test (if it's lower, ema50 necessarily is too).
        ema_test = candidate["ema20_extreme"] < reference["ema_extreme"]
        price_test = candidate["price_extreme"] < reference["price_extreme"]
        # Price clearly contradicts if it is materially HIGHER than the reference
        # high (i.e. made a genuine new high, not progressive weakness — so any
        # EMA reading showing lower is lag/noise and should not carry the leg).
        price_clearly_contradicts = candidate["price_extreme"] > reference["price_extreme"] * (
            1 + CONTRADICTION_TOLERANCE_PCT / 100.0
        )
    return price_test or (ema_test and not price_clearly_contradicts)


def find_five_leg_episodes(hist, ema20, ema50, ema200, min_leg_days: int = 5):
    """
    Returns a list of episode dicts, each with:
      leg1_start, qualified_date, probe_date, x_price, y_price,
      num_legs_observed, status

    status is one of FOUR values, based on where the 50 EMA sits relative to
    the 200 EMA at the moment the pattern qualifies (5th leg confirms):

      NORMAL CASE - 50 EMA is already below 200 EMA at qualification (the
      typical case, since a deep enough multi-leg decline usually drags the
      50 EMA down through the 200 EMA well before the pattern finishes):
        "pattern_forming" - waiting for the 50 EMA to cross back ABOVE the
                             200 EMA (a golden-cross-style recovery signal).
                             X/Y are provisional, still updating.
        "probe_complete"  - that upward cross has happened. X/Y are the
                             lowest 200 EMA / lowest price from leg 1's start
                             through the cross date - locked in.

      RARE CASE - 50 EMA is still ABOVE 200 EMA at qualification (the whole
      5-leg structure played out without ever dragging the 50 EMA below the
      200 EMA - e.g. a shallower pullback within a longer-term uptrend).
      There's no golden cross to wait for here, so instead we use the
      pattern's own lowest point directly as the reentry reference:
        "above_200_forming" - leg 5 (the qualifying down leg) is still in
                               progress. X/Y are provisional, still updating
                               as leg 5 continues.
        "above_200_complete" - leg 5 has finished (price has turned back up
                                into a new leg). X/Y are locked in as of
                                leg 5's end.
    """
    legs = _get_legs(hist, ema20, ema50, min_leg_days)
    episodes = []
    i = 0
    n = len(legs)
    while i < n:
        if legs[i]["direction"] != "down":
            i += 1
            continue
        # The high made during the leg immediately BEFORE leg 1 started (if
        # any) is the pre-existing streak high. If leg 2 rallies back above
        # it, this was never a corrective down-up-down-up-down structure -
        # it is just a continuation of the prior uptrend making new highs -
        # so the whole attempt starting at this leg 1 is invalidated.
        prior_leg = legs[i - 1] if i > 0 else None
        prior_high_ema = prior_leg["ema_extreme"] if prior_leg is not None else None
        prior_high_price = prior_leg["price_extreme"] if prior_leg is not None else None

        chain = [legs[i]]
        chain_indices = [i]
        j = i + 1
        broke_at = None
        invalidated = False
        while j < n and len(chain) < 5:
            candidate = legs[j]
            leg_number = len(chain) + 1
            if leg_number == 2:
                if prior_high_ema is not None:
                    violates_prior_high = (
                        candidate["ema_extreme"] > prior_high_ema
                        or candidate["price_extreme"] > prior_high_price
                    )
                    if violates_prior_high:
                        broke_at = j
                        invalidated = True
                        break
                chain.append(candidate)
                chain_indices.append(j)
                j += 1
                continue
            reference = chain[leg_number - 3]
            if _extends(candidate, reference):
                chain.append(candidate)
                chain_indices.append(j)
                j += 1
            else:
                broke_at = j
                break

        if len(chain) >= 5:
            leg1_start = chain[0]["start"]
            leg5, leg3 = chain[4], chain[2]
            leg5_list_index = chain_indices[4]
            leg5_has_ended = leg5_list_index < n - 1  # is there a leg AFTER leg5 already (leg 6 started)?

            # Vectorized (numpy) instead of a day-by-day Python loop: find the
            # first day within leg5 where the running min (EMA pair or price)
            # clears leg3's floor - same logic, much faster at realistic
            # history lengths.
            leg5_idx = hist.loc[leg5["start"]:leg5["end"]].index
            ema20_leg5 = ema20.loc[leg5_idx].to_numpy()
            ema50_leg5 = ema50.loc[leg5_idx].to_numpy()
            low_leg5 = hist.loc[leg5_idx, "Low"].to_numpy()
            combined_min_leg5 = np.minimum(ema20_leg5, ema50_leg5)
            running_ema_min_leg5 = np.minimum.accumulate(combined_min_leg5)
            running_price_min_leg5 = np.minimum.accumulate(low_leg5)
            qual_mask = (running_ema_min_leg5 < leg3["ema_extreme"]) | (running_price_min_leg5 < leg3["price_extreme"])
            qualified_date = leg5_idx[np.argmax(qual_mask)] if qual_mask.any() else leg5["end"]

            normal_case = ema50.loc[qualified_date] < ema200.loc[qualified_date]

            if normal_case:
                # Wait for the 50 EMA to cross back ABOVE the 200 EMA (golden
                # cross), searched only from qualification onward so an
                # earlier incidental dip doesn't truncate the window early.
                # Vectorized: this loop used to scan day-by-day and could
                # cover YEARS of remaining data when no probe is ever found -
                # a single boolean-mask + argmax is dramatically faster.
                after_idx = hist.index[hist.index >= qualified_date]
                ema50_after = ema50.loc[after_idx].to_numpy()
                ema200_after = ema200.loc[after_idx].to_numpy()
                probe_mask = ema50_after >= ema200_after
                probe_date = after_idx[np.argmax(probe_mask)] if probe_mask.any() else None
                end_for_xy = probe_date if probe_date is not None else hist.index[-1]
                window_idx = hist.loc[leg1_start:end_for_xy].index
                x_price = float(ema200.loc[window_idx].min())
                y_price = float(hist.loc[window_idx, "Low"].min())
                status = "probe_complete" if probe_date is not None else "pattern_forming"
            else:
                # Rare case: 50 EMA never dropped below 200 EMA during this
                # decline. No golden cross to wait for - use the pattern's own
                # low directly. Window ends at leg 5's end if it has already
                # finished, or "now" (still updating) if leg 5 is ongoing.
                probe_date = None
                end_for_xy = leg5["end"] if leg5_has_ended else hist.index[-1]
                window_idx = hist.loc[leg1_start:end_for_xy].index
                x_price = float(ema200.loc[window_idx].min())
                y_price = float(hist.loc[window_idx, "Low"].min())
                status = "above_200_complete" if leg5_has_ended else "above_200_forming"

            episodes.append({
                "leg1_start": leg1_start,
                "qualified_date": qualified_date,
                "probe_date": probe_date,
                "x_price": x_price,
                "y_price": y_price,
                "num_legs_observed": len(chain),
                "status": status,
                # The date X/Y were locked in (whichever branch produced this
                # episode) - the natural anchor for retest tracking once the
                # episode has reached a terminal ("complete") status.
                "completion_date": end_for_xy,
            })
            advance_to = end_for_xy
            next_i = n
            for k in range(i + 1, n):
                if legs[k]["start"] > advance_to:
                    next_i = k
                    break
            i = next_i
        else:
            if broke_at is not None:
                broken_leg = legs[broke_at]
                if broken_leg["direction"] == "down":
                    i = broke_at
                else:
                    i = broke_at + 1
                    while i < n and legs[i]["direction"] != "down":
                        i += 1
            else:
                break
    return episodes
