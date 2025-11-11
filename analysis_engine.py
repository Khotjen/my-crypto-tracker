import pandas as pd
import streamlit as st
from datetime import datetime

@st.cache_data
def fetch_historical_data(_cg_client, coin_id, days):
    try:
        chart_data = _cg_client.get_coin_market_chart_by_id(
            id=coin_id, 
            vs_currency='usd', 
            days=days
        )
        df = pd.DataFrame(chart_data['prices'], columns=['Timestamp', 'Price'])
        df['Date'] = pd.to_datetime(df['Timestamp'], unit='ms').dt.date
        daily_avg_df = df.groupby('Date')[['Price']].mean()
        return daily_avg_df
    except Exception as e:
        st.error(f"Error fetching history for {coin_id}: {e}")
        return pd.DataFrame()

def calculate_portfolio_history(trades_list, cg_client):
    if not trades_list:
        return pd.DataFrame()

    # 1. Konversi list trade (dari DB) ke DataFrame
    trades_df = pd.DataFrame(trades_list)
    # --- PERBAIKAN DI SINI ---
    # Konversi objek 'date' (dari load_trades) ke datetime untuk pandas
    trades_df['date'] = pd.to_datetime(trades_df['date'])
    
    # --- PERBAIKAN DI SINI ---
    start_date = trades_df['date'].min()
    today = datetime.now()
    days_since_start = (today - start_date).days + 2
    unique_coins = trades_df['coin'].unique()

    # 2. Ambil semua riwayat harga
    price_histories = {}
    for coin in unique_coins:
        price_histories[coin] = fetch_historical_data(cg_client, coin, days_since_start)

    # 3. Buat DataFrame holding harian
    all_days_index = pd.date_range(start=start_date, end=today, freq='D').date
    holdings_df = pd.DataFrame(index=all_days_index, columns=unique_coins).fillna(0)

    # --- PERBAIKAN DI SINI ---
    # Hitung perubahan holding harian dari trade
    trade_changes = trades_df.groupby(['date', 'coin'])['amount'].sum()
    trade_changes = trade_changes.unstack(level='coin').reindex(all_days_index).fillna(0)
    
    # Hitung holding kumulatif (apa yang kita miliki setiap hari)
    holdings_df = trade_changes.cumsum()

    # 4. Buat DataFrame harga harian
    prices_df = pd.DataFrame(index=all_days_index, columns=unique_coins).fillna(0)
    for coin in unique_coins:
        if not price_histories[coin].empty:
            prices_df[coin] = price_histories[coin]['Price'].reindex(all_days_index, method='ffill')

    # 5. Hitung nilai portofolio
    daily_value_df = holdings_df * prices_df
    total_value_over_time = daily_value_df.sum(axis=1)
    total_value_over_time = total_value_over_time.to_frame(name="Total Value")
    
    return total_value_over_time
