import pandas as pd
import hourly_price_cache
import price_cache
from vwap_support_resistance_pattern import compute_session_summary, find_vwap_sr_episodes, find_vwap_sr_episodes_upper

symbol = "TCS"
target_date = pd.Timestamp("2026-07-23")

print("=" * 60)
print(f"DIAGNOSTIC: {symbol} on {target_date.strftime('%Y-%m-%d')}")
print("=" * 60)

# 1. Daily data
daily = price_cache.get_full_history(symbol)
print(f"\n1. Daily: {len(daily)} rows, {daily.index.min().date()} to {daily.index.max().date()}")
row = daily.loc[target_date]
print(f"   O={row['Open']:.2f} H={row['High']:.2f} L={row['Low']:.2f} C={row['Close']:.2f}")

# 2. Hourly data
hourly = hourly_price_cache.get_hourly_history(symbol)
hourly_target = hourly[hourly.index.date == target_date.date()]
print(f"\n2. Hourly rows on {target_date.date()}: {len(hourly_target)}")
if len(hourly_target) > 0:
    print(f"   First bar: O={hourly_target.iloc[0]['Open']:.2f} H={hourly_target.iloc[0]['High']:.2f} L={hourly_target.iloc[0]['Low']:.2f} C={hourly_target.iloc[0]['Close']:.2f}")

# 3. Session summary
session_summary = compute_session_summary(hourly, daily)
print(f"\n3. Session summary columns: {list(session_summary.columns)}")
print(f"   Total sessions: {len(session_summary)}")

if target_date in session_summary.index:
    s = session_summary.loc[target_date]
    print(f"\n   ✅ Session found on {target_date.date()}:")
    print(f"   vwap_close={s['vwap_close']:.4f}")
    print(f"   lower_band_close={s['lower_band_close']:.4f}")
    print(f"   upper_band_close={s['upper_band_close']:.4f}")
    print(f"   first_hour_high={s['first_hour_high']:.4f}")
    print(f"   first_hour_low={s['first_hour_low']:.4f}")
    print(f"   has_zero_volume_bar={s.get('has_zero_volume_bar', 'N/A')}")
else:
    print(f"\n   ❌ Session NOT found on {target_date.date()}")
    nearby = session_summary[(session_summary.index >= target_date - pd.Timedelta(days=3)) & 
                              (session_summary.index <= target_date + pd.Timedelta(days=3))]
    print(f"   Nearby sessions: {list(nearby.index.date)}")

# 4. Manual criteria check
if target_date in session_summary.index:
    s = session_summary.loc[target_date]
    d = daily.loc[target_date]
    
    lower_band = s['lower_band_close']
    upper_band = s['upper_band_close']
    close = d['Close']
    fhh = s['first_hour_high']
    fhl = s['first_hour_low']
    
    print(f"\n4. Detection criteria for {target_date.date()}:")
    print(f"   Close={close:.4f}, lower_band={lower_band:.4f}, upper_band={upper_band:.4f}")
    print(f"   first_hour_high={fhh:.4f}, first_hour_low={fhl:.4f}")
    
    # Lower band: lower_band_close > first_hour_high * (1 + 0.5%)
    lower_threshold = fhh * 1.005
    lower_pass = lower_band > lower_threshold
    print(f"\n   Lower band check:")
    print(f"      lower_band_close ({lower_band:.4f}) > first_hour_high*1.005 ({lower_threshold:.4f})? → {lower_pass}")
    
    # Upper band: upper_band_close < first_hour_low * (1 - 0.5%)
    upper_threshold = fhl * 0.995
    upper_pass = upper_band < upper_threshold
    print(f"\n   Upper band check:")
    print(f"      upper_band_close ({upper_band:.4f}) < first_hour_low*0.995 ({upper_threshold:.4f})? → {upper_pass}")
    
    # Zero volume check
    zero_vol = s.get('has_zero_volume_bar', False)
    print(f"\n   has_zero_volume_bar={zero_vol} (must be False to qualify)")

# 5. Full detection
print(f"\n5. Running full detection...")
lower = find_vwap_sr_episodes(daily, session_summary, min_gap_pct=0.5)
upper = find_vwap_sr_episodes_upper(daily, session_summary, min_gap_pct=0.5)
all_eps = lower + upper

target_eps = [e for e in all_eps if pd.Timestamp(e['day_d_date']).date() == target_date.date()]
print(f"   Total episodes: {len(all_eps)}")
print(f"   On {target_date.date()}: {len(target_eps)}")

if target_eps:
    for ep in target_eps:
        print(f"   ✅ {ep['episode_type']}: X={ep['x_price']:.4f}, gap={ep['gap_pct']:.4f}%, status={ep['status']}")
else:
    print(f"   ❌ No episode on target date")
    # Show closest episodes
    closest = sorted(all_eps, key=lambda x: abs((pd.Timestamp(x['day_d_date']) - target_date).days))[:5]
    for ep in closest:
        days = (pd.Timestamp(ep['day_d_date']) - target_date).days
        print(f"      {ep['day_d_date']} ({days:+d}d): {ep['episode_type']} X={ep['x_price']:.2f}")

print(f"\n{'='*60}")