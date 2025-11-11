import pandas as pd
import streamlit as st
from datetime import datetime

# We use @st.cache_data to save the results of this slow API call.
@st.cache_data
def fetch_historical_data(_cg_client, coin_id, days):
    """
    Fetches historical market data for a specific coin.
    We use _cg_client (with an underscore) to tell Streamlit
    that this argument shouldn't be checked for caching.
    """
    try:
        chart_data = _cg_client.get_coin_market_chart_by_id(
            id=coin_id, 
            vs_currency='usd', 
            days=days
        )
        
        # Convert the price data to a Pandas DataFrame
        df = pd.DataFrame(chart_data['prices'], columns=['Timestamp', 'Price'])
        # Convert timestamp to a proper datetime object
        df['Date'] = pd.to_datetime(df['Timestamp'], unit='ms').dt.date
        
        # --- THE FIX IS HERE ---
        # When CoinGecko gives hourly data, we get duplicates for the same date.
        # We fix this by grouping by the Date and getting the *average* price.
        # This ensures we have exactly ONE price entry per day.
        daily_avg_df = df.groupby('Date')[['Price']].mean()
        
        return daily_avg_df
    
    except Exception as e:
        st.error(f"Error fetching history for {coin_id}: {e}")
        return pd.DataFrame() # Return empty DataFrame on failure


def calculate_portfolio_history(trades_list, cg_client):
    """
    Calculates the total value of the portfolio for every day
    since the first trade.
    """
    if not trades_list:
        return pd.DataFrame() # Return empty if no trades

    # 1. Convert trades list to a DataFrame
    trades_df = pd.DataFrame(trades_list)
    # Convert date objects (from load_trades) to datetime
    trades_df['Date'] = pd.to_datetime(trades_df['Date'])
    
    # Find the very first trade date and unique coins
    start_date = trades_df['Date'].min()
    today = datetime.now()
    days_since_start = (today - start_date).days + 2 # +2 to be safe
    unique_coins = trades_df['Coin'].unique()

    # 2. Fetch all price histories
    price_histories = {}
    for coin in unique_coins:
        price_histories[coin] = fetch_historical_data(cg_client, coin, days_since_start)

    # 3. Create a day-by-day holdings DataFrame
    # Create a daily index from the start date to today
    all_days_index = pd.date_range(start=start_date, end=today, freq='D').date
    
    # Create an empty DataFrame to store holdings
    holdings_df = pd.DataFrame(index=all_days_index, columns=unique_coins).fillna(0)

    # Calculate daily holdings changes from trades
    trade_changes = trades_df.groupby(['Date', 'Coin'])['Amount'].sum()
    trade_changes = trade_changes.unstack(level='Coin').reindex(all_days_index).fillna(0)
    
    # Calculate cumulative holdings (what we owned each day)
    holdings_df = trade_changes.cumsum()

    # 4. Create a day-by-day price DataFrame
    prices_df = pd.DataFrame(index=all_days_index, columns=unique_coins).fillna(0)
    for coin in unique_coins:
        if not price_histories[coin].empty:
            # Map the prices we fetched onto our daily index
            prices_df[coin] = price_histories[coin]['Price'].reindex(all_days_index, method='ffill')

    # 5. Calculate portfolio value
    # Multiply holdings by prices to get value
    daily_value_df = holdings_df * prices_df
    
    # Sum up all coins to get total portfolio value
    total_value_over_time = daily_value_df.sum(axis=1)
    total_value_over_time = total_value_over_time.to_frame(name="Total Value")
    
    return total_value_over_time