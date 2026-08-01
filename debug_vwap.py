import pandas as pd
import levels_store
import strategy_vwap_sr as vwap

# 1. Test a small batch first
print("=== Running VWAP scan on 5 symbols ===")
result = vwap.run_vwap_sr_strategy(symbols=["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"])
print(f"Signal: {result.signal}, Trend: {result.trend}")
print(f"Metadata: {result.metadata}")
print(f"Raw data shape: {result.raw_data.shape}")
if not result.raw_data.empty:
    print(result.raw_data.head())
    print(f"Statuses: {result.raw_data['status'].value_counts().to_dict()}")

# 2. Check what's actually in the DB
print("\n=== Reading back from SQLite ===")
df = levels_store.get_vwap_sr_episodes()
print(f"DB rows: {len(df)}")
if not df.empty:
    print(df.head())
    print(f"Symbols in DB: {df['symbol'].unique()[:10]}")
else:
    print("Table is empty.")

# 3. Check if the table even exists
print("\n=== Checking schema ===")
import sqlite3
conn = sqlite3.connect("levels_ledger.db")
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(f"Tables: {tables}")
if "vwap_sr_episodes" in tables:
    count = conn.execute("SELECT COUNT(*) FROM vwap_sr_episodes").fetchone()[0]
    print(f"vwap_sr_episodes row count: {count}")
conn.close()