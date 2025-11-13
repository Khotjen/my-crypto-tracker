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

# --- ID DOMPET FUTURES (Kita anggap hanya ada 1 baris di tabel wallet) ---
FUTURES_WALLET_ID = 1 

# --- KONEKSI KE SUPABASE ---
@st.cache_resource
def init_supabase_client():
    try:
        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return client
    except Exception as e:
        st.error(f"Gagal terhubung ke Supabase. Error: {e}")
        st.stop()

client = init_supabase_client()

# --- FUNGSI DATABASE (v11.1) ---
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

@st.cache_data(ttl=10)
def load_futures_wallet_balance():
    """Mengambil saldo 'tersedia' dari tabel 'futures_wallet'."""
    try:
        response = client.table('futures_wallet').select("balance").eq('id', FUTURES_WALLET_ID).execute()
        if response.data:
            return response.data[0]['balance']
        else:
            return 0.0
    except Exception as e:
        st.error(f"Error membaca 'futures_wallet': {e}")
        return 0.0

def update_futures_wallet_balance(new_balance):
    """Meng-update saldo 'tersedia' di tabel 'futures_wallet'."""
    try:
        response = client.table('futures_wallet').select("id").eq('id', FUTURES_WALLET_ID).execute()
        if response.data:
            client.table('futures_wallet').update({"balance": new_balance}).eq('id', FUTURES_WALLET_ID).execute()
        else:
            client.table('futures_wallet').insert({"id": FUTURES_WALLET_ID, "balance": new_balance}).execute()
        st.cache_data.clear() 
    except Exception as e:
        st.error(f"Error meng-update 'futures_wallet': {e}")

# 1. --- Initialize API Client ---
try:
    cg = CoinGeckoAPI(); cg.ping() 
except Exception as e:
    st.error(f"Error connecting to CoinGecko API: {e}"); st.stop()

# --- ====================================================== ---
# --- FUNGSI BARU v11.3: Ambil Data Grafik Global ---
# --- ====================================================== ---
@st.cache_data(ttl=600) # Cache data ini selama 10 menit
def get_global_market_data():
    """Mengambil data harga BTC (7h) dan Dominasi BTC (30h)."""
    try:
        # 1. Ambil data harga BTC 7 hari (interval per jam)
        btc_price_data = cg.get_coin_market_chart_by_id('bitcoin', 'usd', 7)
        price_df = pd.DataFrame(btc_price_data['prices'], columns=['timestamp', 'price'])
        price_df['date'] = pd.to_datetime(price_df['timestamp'], unit='ms')
        
        # 2. Ambil data Dominasi BTC 30 hari (interval harian)
        # Kita perlu mengambil 'market_caps' untuk ini
        btc_dom_data = cg.get_coin_market_chart_by_id('bitcoin', 'usd', 30)
        dom_df = pd.DataFrame(btc_dom_data['market_caps'], columns=['timestamp', 'market_cap_btc'])
        
        global_data = cg.get_global_market_chart_range(
            from_timestamp=(datetime.now() - pd.Timedelta(days=30)).timestamp(),
            to_timestamp=datetime.now().timestamp()
        )
        global_mcap_df = pd.DataFrame(global_data['market_caps'], columns=['timestamp', 'market_cap_global'])

        # Gabungkan data dominasi
        dom_df = pd.merge(dom_df, global_mcap_df, on='timestamp', how='inner')
        dom_df['date'] = pd.to_datetime(dom_df['timestamp'], unit='ms')
        dom_df['btc_dominance'] = (dom_df['market_cap_btc'] / dom_df['market_cap_global']) * 100
        
        return price_df, dom_df

    except Exception as e:
        st.warning(f"Gagal mengambil data pasar global: {e}")
        return pd.DataFrame(), pd.DataFrame()

# 2. --- Initialize Session State ---
if 'trades' not in st.session_state:
    st.session_state.trades = load_trades()
if 'futures_positions' not in st.session_state:
    st.session_state.futures_positions = load_futures_positions()
if 'futures_balance' not in st.session_state:
    st.session_state.futures_balance = load_futures_wallet_balance()

# --- (Kalkulasi Portofolio Anda tidak berubah) ---
total_spot_value = 0.0; total_spot_pl = 0.0
summary_df = pd.DataFrame(); portfolio_coins = []; futures_coins = []
available_futures_balance = st.session_state.futures_balance
total_futures_margin_used = 0.0
total_futures_pnl = 0.0
futures_df = pd.DataFrame()

if st.session_state.trades:
    df = pd.DataFrame(st.session_state.trades)
    df['Amount'] = pd.to_numeric(df['amount'])
    df['Total Cost (USD)'] = pd.to_numeric(df['total_cost_usd'])
    buys = df[df['type'] == 'Buy']; sells = df[df['type'] == 'Sell']
    buy_summary = buys.groupby('coin')['Amount'].sum()
    sell_summary = sells.groupby('coin')['Amount'].sum()
    holdings_df = (buy_summary.subtract(sell_summary, fill_value=0)).to_frame(name="Holdings")
    holdings_df = holdings_df[holdings_df['Holdings'] > 0.000001]
    total_buy_cost = buys.groupby('coin')['Total Cost (USD)'].sum()
    total_buy_amount = buys.groupby('coin')['Amount'].sum()
    avg_buy_cost_df = (total_buy_cost / total_buy_amount).to_frame(name="Avg. Buy Price")
    summary_df = pd.merge(holdings_df, avg_buy_cost_df, left_index=True, right_index=True, how='left')
    portfolio_coins = summary_df.index.unique().tolist()

if st.session_state.futures_positions:
    futures_coins = list(set([pos['coin_id'] for pos in st.session_state.futures_positions]))
    total_futures_margin_used = sum(pos['margin'] for pos in st.session_state.futures_positions)

all_coins = list(set(portfolio_coins + futures_coins + ['tether']))
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
        live_price = 1.0 if pos['coin_id'] == 'tether' else all_live_prices.get(pos['coin_id'], 0)
        pos_size_usd = pos['margin'] * pos['leverage']
        pos_size_coins = pos_size_usd / pos['entry_price']
        
        if pos['direction'] == 'Long':
            liq_price = pos['entry_price'] * (1 - (1 / pos['leverage']))
            pnl_usd = (live_price - pos['entry_price']) * pos_size_coins
        else:
            liq_price = pos['entry_price'] * (1 + (1 / pos['leverage']))
            pnl_usd = (pos['entry_price'] - live_price) * pos_size_coins
        
        pnl_perc = (pnl_usd / pos['margin']) * 100 if pos['margin'] != 0 else 0
        total_futures_pnl += pnl_usd
        
        positions_to_display.append({
            "DB_ID": pos['id'], "Coin": pos['coin_id'], "Direction": pos['direction'], "Size (USD)": pos_size_usd,
            "Margin": pos['margin'], "Leverage": f"{pos['leverage']}x", "Entry Price": pos['entry_price'],
            "Live Price": live_price, "P/L (USD)": pnl_usd, "P/L (%)": pnl_perc, "Liq. Price": liq_price
        })
    futures_df = pd.DataFrame(positions_to_display)

total_futures_equity = available_futures_balance + total_futures_margin_used + total_futures_pnl
grand_total = total_spot_value + total_futures_equity

# 4. --- ===================================================== ---
# --- TAMPILAN APLIKASI v11.3 ---
# --- ===================================================== ---

st.set_page_config(page_title="My Crypto Tracker", page_icon="🚀", layout="wide")
st.title("🚀 My Supercharged Crypto Tracker (Phase 11.3)")

# --- ====================================================== ---
# --- BAGIAN BARU v11.3: Dashboard Pasar Global ---
# --- ====================================================== ---
st.subheader("Global Market Overview")
btc_price_chart_df, btc_dom_chart_df = get_global_market_data()

chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    if not btc_price_chart_df.empty:
        # Ambil harga BTC saat ini dari data
        current_btc_price = btc_price_chart_df.iloc[-1]['price']
        st.metric(label="Current Bitcoin Price", value=f"${current_btc_price:,.2f}")
        
        fig_price = px.line(btc_price_chart_df, x='date', y='price', title='BTC Price (7-Day)')
        fig_price.update_layout(xaxis_title=None, yaxis_title='Price (USD)', yaxis_tickprefix='$', yaxis_tickformat = ',.2f')
        st.plotly_chart(fig_price, width='stretch')
    else:
        st.info("Tidak dapat memuat grafik harga BTC.")

with chart_col2:
    if not btc_dom_chart_df.empty:
        # Ambil dominasi BTC saat ini dari data
        current_btc_dom = btc_dom_chart_df.iloc[-1]['btc_dominance']
        st.metric(label="Current BTC Dominance", value=f"{current_btc_dom:.2f}%")
        
        fig_dom = px.line(btc_dom_chart_df, x='date', y='btc_dominance', title='BTC Dominance (30-Day)')
        fig_dom.update_layout(xaxis_title=None, yaxis_title='Dominance (%)', yaxis_ticksuffix='%')
        st.plotly_chart(fig_dom, width='stretch')
    else:
        st.info("Tidak dapat memuat grafik dominasi BTC.")
st.divider()
# --- AKHIR BAGIAN BARU ---

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

st.subheader("My Futures Wallet & Positions")
f_col1, f_col2, f_col3 = st.columns(3)
f_col1.metric(label="Total Futures Equity", value=f"${total_futures_equity:,.2f}", delta=f"${total_futures_pnl:,.2f} (Total P/L)")
f_col2.metric(label="Margin Terpakai", value=f"${total_futures_margin_used:,.2f}")
f_col3.metric(label="Margin Tersedia (di Dompet)", value=f"${available_futures_balance:,.2f}")

if futures_df.empty:
    st.info("You have no open futures positions. Add one below.")
else:
    st.dataframe(futures_df.style.format({
        'Size (USD)': '${:,.2f}', 'Margin': '${:,.2f}', 'Entry Price': '${:,.4f}',
        'Live Price': '${:,.4f}', 'P/L (USD)': '${:,.2f}', 'P/L (%)': '{:,.2f}%',
        'Liq. Price': '${:,.4f}'
    }), width='stretch', hide_index=True)

    st.subheader("Close a Position & Return to Wallet")
    with st.form("close_form"):
        pos_col_1, pos_col_2 = st.columns([1, 3])
        with pos_col_1:
            position_id_to_close = st.number_input("Position DB_ID to close:", min_value=1, step=1)
        with pos_col_2:
            close_button = st.form_submit_button("Close & Return to Futures Wallet")
        
        if close_button:
            try:
                pos_data_to_close = futures_df[futures_df['DB_ID'] == position_id_to_close].to_dict('records')
                if not pos_data_to_close:
                    st.error(f"Error: Tidak bisa menemukan posisi dengan DB_ID {position_id_to_close}.")
                else:
                    pos_data = pos_data_to_close[0]
                    final_pnl = pos_data['P/L (USD)']; original_margin = pos_data['Margin']
                    total_cash_back = original_margin + final_pnl
                    if total_cash_back < 0: total_cash_back = 0
                    current_balance = load_futures_wallet_balance()
                    new_balance = current_balance + total_cash_back
                    update_futures_wallet_balance(new_balance)
                    client.table('futures_positions').delete().eq('id', int(position_id_to_close)).execute()
                    st.success(f"Posisi {position_id_to_close} ditutup. Total ${total_cash_back:,.2f} dikembalikan ke Dompet Futures.")
                    st.session_state.futures_positions = load_futures_positions()
                    st.session_state.futures_balance = load_futures_wallet_balance()
                    st.rerun() 
            except Exception as e:
                st.error(f"Gagal menutup posisi: {e}"); st.exception(e)
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
                new_trade = {"date": str(trade_date), "coin": coin_id, "type": trade_type, "amount": amount, "price_per_coin": price_per_coin, "total_cost_usd": total_cost}
                try:
                    client.table('spot_trades').insert(new_trade).execute()
                    st.success("Spot trade berhasil disimpan!"); st.session_state.trades = load_trades(); st.rerun()
                except Exception as e:
                    st.error(f"Gagal menyimpan trade: {e}")

with form_col2:
    st.subheader("Log a New Futures Position")
    st.info(f"Dompet Tersedia: ${available_futures_balance:,.2f}")
    with st.form("futures_form", clear_on_submit=True):
        f2_col1, f2_col2 = st.columns(2)
        with f2_col1: fut_coin_id = st.text_input("Futures Coin ID").lower()
        with f2_col2: fut_direction = st.selectbox("Direction", ["Long", "Short"])
        f2_col3, f2_col4, f2_col5 = st.columns(3)
        with f2_col3: fut_size_usd = st.number_input("Size (USD)", min_value=1.0, format="%.2f")
        with f2_col4: fut_leverage = st.number_input("Leverage (e.g., 25)", min_value=1, max_value=250, step=1)
        with f2_col5: fut_entry_price = st.number_input("Entry Price (USD)", min_value=0.000001, format="%.8f")
        fut_submitted = st.form_submit_button("Open Futures Position")
        
        if fut_submitted:
            if not fut_coin_id or fut_entry_price == 0 or fut_size_usd == 0:
                st.error("Harap isi semua field.")
            else:
                margin_needed = fut_size_usd / fut_leverage
                if available_futures_balance < margin_needed:
                    st.error(f"Margin tidak cukup. Butuh: ${margin_needed:,.2f}, Tersedia: ${available_futures_balance:,.2f}")
                else:
                    new_position = {"coin_id": fut_coin_id, "direction": fut_direction, "entry_price": fut_entry_price, "margin": margin_needed, "leverage": int(fut_leverage)}
                    try:
                        new_balance = available_futures_balance - margin_needed
                        update_futures_wallet_balance(new_balance)
                        client.table('futures_positions').insert(new_position).execute()
                        st.success(f"Posisi dibuka! ${margin_needed:,.2f} margin telah dipindahkan dari dompet.")
                        st.session_state.futures_positions = load_futures_positions()
                        st.session_state.futures_balance = new_balance
                        st.rerun()
                    except Exception as e:
                        st.error(f"Gagal membuka posisi: {e}"); st.exception(e)

st.divider()
st.subheader("Futures Wallet Management")
st.info(f"Saldo Dompet Tersedia Saat Ini: ${available_futures_balance:,.2f}")
with st.form("wallet_form"):
    wm_col1, wm_col2, wm_col3 = st.columns(3)
    with wm_col1:
        transfer_amount = st.number_input("Amount (USD)", min_value=0.01)
    with wm_col2:
        deposit_button = st.form_submit_button("Deposit to Futures Wallet")
    with wm_col3:
        withdraw_button = st.form_submit_button("Withdraw from Futures Wallet")
    if deposit_button:
        new_balance = available_futures_balance + transfer_amount
        update_futures_wallet_balance(new_balance)
        st.success(f"Deposit ${transfer_amount} berhasil. Saldo baru: ${new_balance:,.2f}")
        st.session_state.futures_balance = new_balance
        st.rerun()
    if withdraw_button:
        if available_futures_balance < transfer_amount:
            st.error("Dana tidak cukup untuk ditarik.")
        else:
            new_balance = available_futures_balance - transfer_amount
            update_futures_wallet_balance(new_balance)
            st.success(f"Withdraw ${transfer_amount} berhasil. Saldo baru: ${new_balance:,.2f}")
            st.session_state.futures_balance = new_balance
            st.rerun()
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
                st.success(f"Trade ID {trade_id_to_delete} dihapus."); st.session_state.trades = load_trades(); st.rerun()
            except Exception as e:
                st.error(f"Gagal menghapus trade: {e}")

st.divider()
st.subheader("--- 📛 ZONA BAHAYA 📛 ---")
st.warning("Tindakan di bawah ini permanen dan tidak bisa dibatalkan. Data Anda akan hilang selamanya.")
col_danger_1, col_danger_2 = st.columns(2)
with col_danger_1:
    with st.form("clear_spot_form"):
        st.write("Tekan tombol ini untuk menghapus **SEMUA** riwayat trade Spot Anda secara permanen.")
        clear_spot_button = st.form_submit_button("🔥 HAPUS SEMUA SPOT TRADES 🔥", type="primary")
        if clear_spot_button:
            try:
                client.table('spot_trades').delete().gt('id', 0).execute() 
                st.session_state.trades = []
                st.success("SEMUA trade spot telah dihapus dari database.")
                st.rerun()
            except Exception as e:
                st.error(f"Gagal menghapus spot: {e}")

with col_danger_2:
    with st.form("clear_futures_form"):
        st.write("Tekan ini untuk menghapus **SEMUA** posisi Futures DAN mengosongkan Dompet Futures Anda ke $0.")
        clear_futures_button = st.form_submit_button("🔥 HAPUS SEMUA FUTURES 🔥", type="primary")
        if clear_futures_button:
            try:
                client.table('futures_positions').delete().gt('id', 0).execute()
                update_futures_wallet_balance(0.0)
                st.session_state.futures_positions = []
                st.session_state.futures_balance = 0.0
                st.success("SEMUA posisi futures DAN saldo dompet telah dihapus.")
                st.rerun()
            except Exception as e:
                st.error(f"Gagal menghapus futures: {e}")
