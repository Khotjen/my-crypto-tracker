import streamlit as st
from pycoingecko import CoinGeckoAPI
import pandas as pd
from datetime import datetime
import plotly.express as px
import analysis_engine as engine
from supabase import create_client, Client

# --- ====================================================== ---
# --- KUNCI DIAMBIL DARI STREAMLIT SECRETS (AMAN) ---
# --- ====================================================== ---
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except KeyError:
    st.error("ERROR: Supabase URL/Key tidak ditemukan. Atur di 'Settings > Secrets' di Streamlit Cloud.")
    st.stop()
# --- ====================================================== ---


# --- KONEKSI KE SUPABASE ---
@st.cache_resource
def init_supabase_client():
    """Membuat dan meng-cache koneksi Supabase agar tidak dibuat ulang setiap saat."""
    try:
        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return client
    except Exception as e:
        st.error(f"Gagal terhubung ke Supabase. Error: {e}")
        st.stop()

client = init_supabase_client()

# --- FUNGSI DATABASE (Perbaikan 'date' huruf kecil) ---
def load_trades():
    """Mengambil semua trade dari tabel 'spot_trades'."""
    try:
        response = client.table('spot_trades').select("*").execute()
        data = response.data
        for trade in data:
            trade['date'] = datetime.strptime(trade['date'], '%Y-%m-%d').date()
        return data
    except Exception as e:
        st.error(f"Error membaca 'spot_trades': {e}"); return []

def load_futures_positions():
    """Mengambil semua posisi dari tabel 'futures_positions'."""
    try:
        response = client.table('futures_positions').select("*").execute()
        return response.data
    except Exception as e:
        st.error(f"Error membaca 'futures_positions': {e}"); return []

# 1. --- Initialize API Client ---
try:
    cg = CoinGeckoAPI(); cg.ping() 
except Exception as e:
    st.error(f"Error connecting to CoinGecko API: {e}"); st.stop()

# 2. --- Initialize Session State (Sekarang memuat dari Supabase) ---
if 'trades' not in st.session_state:
    st.session_state.trades = load_trades()
if 'futures_positions' not in st.session_state:
    st.session_state.futures_positions = load_futures_positions()

# 3. --- ====================================================== ---
# --- SEMUA KALKULASI (Memperbaiki nama kolom DB) ---
# --- ====================================================== ---
total_spot_value = 0.0; total_spot_pl = 0.0; total_futures_equity = 0.0
total_futures_margin = 0.0; total_futures_pnl = 0.0; summary_df = pd.DataFrame()
futures_df = pd.DataFrame(); all_live_prices = {}; portfolio_coins = []; futures_coins = []

if st.session_state.trades:
    df = pd.DataFrame(st.session_state.trades)
    df['Amount'] = pd.to_numeric(df['amount']) # Nama kolom DB
    df['Total Cost (USD)'] = pd.to_numeric(df['total_cost_usd']) # Nama kolom DB
    buys = df[df['type'] == 'Buy']; sells = df[df['type'] == 'Sell'] # Nama kolom DB
    buy_summary = buys.groupby('coin')['Amount'].sum() # Nama kolom DB
    sell_summary = sells.groupby('coin')['Amount'].sum()
    holdings_df = (buy_summary.subtract(sell_summary, fill_value=0)).to_frame(name="Holdings")
    holdings_df = holdings_df[holdings_df['Holdings'] > 0.000001]
    
    total_buy_cost = buys.groupby('coin')['Total Cost (USD)'].sum()
    total_buy_amount = buys.groupby('coin')['Amount'].sum()
    avg_buy_cost_df = (total_buy_cost / total_buy_amount).to_frame(name="Avg. Buy Price")
    
    # --- INI DIA PERBAIKANNYA (baris 103) ---
    # Mengganti `left_on='coin'` dengan `left_index=True`
    summary_df = pd.merge(holdings_df, avg_buy_cost_df, left_index=True, right_index=True, how='left')
    
    portfolio_coins = summary_df.index.unique().tolist()

if st.session_state.futures_positions:
    futures_coins = list(set([pos['coin_id'] for pos in st.session_state.futures_positions]))

all_coins = list(set(portfolio_coins + futures_coins))
if all_coins:
    try:
        price_data = cg.get_price(ids=all_coins, vs_currencies='usd')
        all_live_prices = {coin: data.get('usd', 0) for coin, data in price_data.items()}
    except Exception as e:
        st.error(f"Error fetching live prices: {e}")

if not summary_df.empty:
    summary_df['Live Price'] = summary_df.index.map(lambda coin: all_live_prices.get(coin, 0))
    summary_df['Current Value (USD)'] = summary_df['Holdings'] * summary_df['Live Price']
    summary_df['P/L (USD)'] = summary_df['Current Value (USD)'] - (summary_df['Holdings'] * summary_df['Avg. Buy Price'])
    total_spot_value = summary_df['Current Value (USD)'].sum()
    total_spot_pl = summary_df['P/L (USD)'].sum()

if st.session_state.futures_positions:
    positions_to_display = []
    for pos in st.session_state.futures_positions:
        live_price = all_live_prices.get(pos['coin_id'], 0)
        pos_size_usd = pos['margin'] * pos['leverage']
        pos_size_coins = pos_size_usd / pos['entry_price']
        
        if pos['direction'] == 'Long':
            liq_price = pos['entry_price'] * (1 - (1 / pos['leverage']))
            pnl_usd = (live_price - pos['entry_price']) * pos_size_coins
        else:
            liq_price = pos['entry_price'] * (1 + (1 / pos['leverage']))
            pnl_usd = (pos['entry_price'] - live_price) * pos_size_coins
        
        pnl_perc = (pnl_usd / pos['margin']) * 100
        total_futures_margin += pos['margin']
        total_futures_pnl += pnl_usd
        
        positions_to_display.append({
            "DB_ID": pos['id'], "Coin": pos['coin_id'], "Direction": pos['direction'], "Size (USD)": pos_size_usd,
            "Margin": pos['margin'], "Leverage": f"{pos['leverage']}x", "Entry Price": pos['entry_price'],
            "Live Price": live_price, "P/L (USD)": pnl_usd, "P/L (%)": pnl_perc, "Liq. Price": liq_price
        })
    
    futures_df = pd.DataFrame(positions_to_display)
    total_futures_equity = total_futures_margin + total_futures_pnl

grand_total = total_spot_value + total_futures_equity

# 4. --- ===================================================== ---
# --- TAMPILAN APLIKASI (Tidak ada yang berubah) ---
# --- ===================================================== ---

st.set_page_config(page_title="My Crypto Tracker", page_icon="🚀", layout="wide")
st.title("🚀 My Supercharged Crypto Tracker (Phase 9.3 - Cloud Ready)")

st.subheader("Total Portfolio Value")
st.metric(label="Total Combined Equity (Spot + Futures)", value=f"${grand_total:,.2f}", delta=f"${total_spot_pl + total_futures_pnl:,.2f} (Total P/L)")
st.divider()

st.subheader("My Spot Portfolio")
if summary_df.empty:
    st.info("Your spot portfolio is empty. Add trades below.")
else:
    st.metric(label="Total Spot Value", value=f"${total_spot_value:,.2f}", delta=f"${total_spot_pl:,.2f} (Total P/L)")
    chart_col, data_col = st.columns([0.4, 0.6])
    with chart_col:
        st.subheader("Spot Allocation")
        # Kode pai ini sekarang seharusnya aman karena summary_df sudah benar
        pie_df = summary_df.reset_index().rename(columns={'coin': 'Coin'})
        fig = px.pie(pie_df, values='Current Value (USD)', names='Coin', title='Spot Allocation')
        fig.update_traces(textposition='inside', textinfo='percent+label')
        st.plotly_chart(fig, width='stretch')
    with data_col:
        st.subheader("Spot Holdings")
        display_df = summary_df.reset_index().rename(columns={'coin': 'Coin'})
        st.dataframe(display_df.style.format({
            'Holdings': '{:,.8f}', 'Avg. Buy Price': '${:,.4f}', 'Live Price': '${:,.4f}',
            'Current Value (USD)': '${:,.2f}', 'P/L (USD)': '${:,.2f}'
        }), width='stretch')
st.divider()

st.subheader("My Futures Positions")
if futures_df.empty:
    st.info("You have no open futures positions. Add one below.")
else:
    fut_col1, fut_col2 = st.columns(2)
    with fut_col1:
        st.metric(label="Total Futures Equity (Margin + P/L)", value=f"${total_futures_equity:,.2f}", delta=f"${total_futures_pnl:,.2f} (Total P/L)")
    with fut_col2:
        st.metric(label="Total Margin Deployed", value=f"${total_futures_margin:,.2f}")
        
    st.dataframe(futures_df.style.format({
        'Size (USD)': '${:,.2f}', 'Margin': '${:,.2f}', 'Entry Price': '${:,.4f}',
        'Live Price': '${:,.4f}', 'P/L (USD)': '${:,.2f}', 'P/L (%)': '{:,.2f}%',
        'Liq. Price': '${:,.4f}'
    }), width='stretch', hide_index=True)

    st.subheader("Close a Position")
    with st.form("close_form"):
        pos_col_1, pos_col_2 = st.columns([1, 3])
        with pos_col_1:
            # Menggunakan 'DB_ID' dari DataFrame untuk kejelasan
            position_id_to_close = st.number_input("Position DB_ID to close:", min_value=1, step=1)
        with pos_col_2:
            close_button = st.form_submit_button("Close Position")
        if close_button:
            try:
                client.table('futures_positions').delete().eq('id', int(position_id_to_close)).execute()
                st.success(f"Position ID {position_id_to_close} ditutup.")
                st.session_state.futures_positions = load_futures_positions()
                st.rerun()
            except Exception as e:
                st.error(f"Gagal menutup posisi: {e}")
st.divider()

st.subheader("Spot Portfolio Historical Performance")
if not st.session_state.trades:
    st.info("Add spot trades to see historical performance.")
else:
    if st.button("Generate Spot Performance Chart"):
        with st.spinner("Crunching spot trade history..."):
            history_df = engine.calculate_portfolio_history(st.session_state.trades, cg)
            if history_df.empty: st.warning("Could not generate history.")
            else:
                fig = px.line(history_df, y='Total Value', title='Spot Portfolio Value Over Time')
                fig.update_layout(xaxis_title='Date', yaxis_title='Portfolio Value (USD)', yaxis_tickprefix = '$', yaxis_tickformat = ',.2f')
                st.plotly_chart(fig, width='stretch')
st.divider()

form_col1, form_col2 = st.columns(2)
with form_col1:
    st.subheader("Log a New Spot Trade")
    with st.form("trade_form", clear_on_submit=True):
        f1_col1, f1_col2, f1_col3 = st.columns(3)
        with f1_col1: trade_date = st.date_input("Trade Date")
        with f1_col2: coin_id = st.text_input("Spot Coin ID").lower()
        with f1_col3: trade_type = st.selectbox("Trade Type", ["Buy", "Sell"])
        f1_col4, f1_col5 = st.columns(2)
        with f1_col4: amount = st.number_input("Amount of Coin", min_value=0.0, format="%.8f")
        with f1_col5: price_per_coin = st.number_input("Price per Coin (USD)", min_value=0.0, format="%.4f")
        submitted = st.form_submit_button("Add Spot Trade")
        
        if submitted:
            if not coin_id: st.error("Please enter a Coin ID.")
            else:
                total_cost = amount * price_per_coin
                new_trade = {
                    "date": str(trade_date), "coin": coin_id, "type": trade_type, 
                    "amount": amount, "price_per_coin": price_per_coin, "total_cost_usd": total_cost
                }
                try:
                    client.table('spot_trades').insert(new_trade).execute()
                    st.success("Spot trade berhasil disimpan ke database!")
                    st.session_state.trades = load_trades()
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal menyimpan trade: {e}")

with form_col2:
    st.subheader("Log a New Futures Position")
    with st.form("futures_form", clear_on_submit=True):
        f2_col1, f2_col2 = st.columns(2)
        with f2_col1: fut_coin_id = st.text_input("Futures Coin ID").lower()
        with f2_col2: fut_direction = st.selectbox("Direction", ["Long", "Short"])
        f2_col3, f2_col4, f2_col5 = st.columns(3)
        with f2_col3: fut_entry_price = st.number_input("Entry Price (USD)", min_value=0.0, format="%.4f")
        with f2_col4: fut_margin = st.number_input("Margin (USD)", min_value=0.01, format="%.2f")
        with f2_col5: fut_leverage = st.number_input("Leverage (e.g., 25)", min_value=1, max_value=250, step=1)
        fut_submitted = st.form_submit_button("Open Futures Position")
        
        if fut_submitted:
            if not fut_coin_id or fut_entry_price == 0 or fut_margin == 0: st.error("Harap isi semua field.")
            else:
                new_position = {
                    "coin_id": fut_coin_id, "direction": fut_direction, "entry_price": fut_entry_price,
                    "margin": fut_margin, "leverage": fut_leverage
                }
                try:
                    client.table('futures_positions').insert(new_position).execute()
                    st.success("Posisi futures berhasil disimpan ke database!")
                    st.session_state.futures_positions = load_futures_positions()
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal membuka posisi: {e}")

st.divider()
st.subheader("My Full Spot Trade Log (From Database)")
if not st.session_state.trades:
    st.info("Log trade spot Anda kosong.")
else:
    log_df = pd.DataFrame(st.session_state.trades).rename(columns={'id': 'DB_ID'})
    st.dataframe(log_df, width='stretch')
    
    st.subheader("Delete a Spot Trade")
    with st.form("delete_spot_form"):
        del_col_1, del_col_2 = st.columns([1, 3])
        with del_col_1:
            trade_id_to_delete = st.number_input("Trade DB_ID to delete:", min_value=1, step=1)
        with del_col_2:
            delete_button = st.form_submit_button("Delete Spot Trade")
        
        if delete_button:
            try:
                client.table('spot_trades').delete().eq('id', int(trade_id_to_delete)).execute()
                st.success(f"Trade ID {trade_id_to_delete} dihapus.")
                st.session_state.trades = load_trades()
                st.rerun()
            except Exception as e:
                st.error(f"Gagal menghapus trade: {e}")
