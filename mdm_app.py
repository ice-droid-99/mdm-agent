
import streamlit as st
import snowflake.connector
import pandas as pd

# ─── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MDM Agent · Snowflake",
    page_icon="❄️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ─── Minimal CSS (safe for Streamlit Cloud) ────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700;800&display=swap');

html, body, [data-testid="stAppViewContainer"] {
    background-color: #0d1117 !important;
}
[data-testid="stAppViewContainer"] > .main {
    background-color: #0d1117 !important;
}
[data-testid="stHeader"] { background: transparent !important; }
#MainMenu, footer { visibility: hidden; }

h1, h2, h3 { font-family: 'Syne', sans-serif !important; }

[data-testid="stTextInput"] input {
    background-color: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
    color: #e6edf3 !important;
    font-family: 'DM Mono', monospace !important;
}
[data-testid="stTextInput"] input:focus {
    border-color: #29b6f6 !important;
    box-shadow: 0 0 0 2px rgba(41,182,246,0.15) !important;
}
[data-testid="stSelectbox"] > div > div {
    background-color: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
    color: #e6edf3 !important;
}
[data-testid="stButton"] > button {
    background: linear-gradient(135deg, #29b6f6, #6366f1) !important;
    color: white !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 700 !important;
    width: 100% !important;
}
[data-testid="stButton"] > button:hover {
    opacity: 0.85 !important;
}
[data-testid="stMetric"] {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 16px !important;
}
[data-testid="stMetricLabel"] { color: #8b949e !important; font-size: 12px !important; }
[data-testid="stMetricValue"] { color: #29b6f6 !important; font-weight: 800 !important; }
[data-testid="stDataFrame"] {
    border: 1px solid #30363d !important;
    border-radius: 10px !important;
}
hr { border-color: #21262d !important; }
[data-testid="stTextInput"] label,
[data-testid="stSelectbox"] label {
    color: #8b949e !important;
    font-family: 'DM Mono', monospace !important;
    font-size: 12px !important;
    text-transform: uppercase !important;
    letter-spacing: 1px !important;
}
</style>
""", unsafe_allow_html=True)


# ─── Session State Defaults ────────────────────────────────────────────────────
defaults = {
    "page": "login",
    "sf_conn": None,
    "sf_account": "",
    "sf_user": "",
    "warehouses": [],
    "databases": [],
    "schemas": [],
    "tables": [],
    "selected_wh": None,
    "selected_db": None,
    "selected_schema": None,
    "selected_table": None,
    "df": None,
    "total_rows": 0,
    "current_page": 1,
    "rows_per_page": 50,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ─── Snowflake Helpers ─────────────────────────────────────────────────────────
def sf_connect(account, user, password):
    return snowflake.connector.connect(
        account=account,
        user=user,
        password=password,
        login_timeout=15
    )

def sf_query(sql):
    cur = st.session_state.sf_conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description] if cur.description else []
    cur.close()
    return pd.DataFrame(rows, columns=cols)

def fetch_table_page(db, schema, table, limit, offset):
    return sf_query(f'SELECT * FROM "{db}"."{schema}"."{table}" LIMIT {limit} OFFSET {offset}')

def fetch_count(db, schema, table):
    r = sf_query(f'SELECT COUNT(*) AS C FROM "{db}"."{schema}"."{table}"')
    return int(r["C"].iloc[0])


# ══════════════════════════════════════════════════════════════════════
# PAGE 1 — LOGIN
# ══════════════════════════════════════════════════════════════════════
def page_login():
    st.markdown("<br><br>", unsafe_allow_html=True)
    _, center, _ = st.columns([1, 1.2, 1])

    with center:
        st.markdown("""
        <div style="text-align:center; margin-bottom:28px;">
            <div style="font-size:52px;">❄️</div>
            <h1 style="color:#e6edf3; font-size:26px; margin:8px 0 4px;">MDM Agent</h1>
            <p style="color:#8b949e; font-family:'DM Mono',monospace; font-size:12px; margin:0;">
                Connect your Snowflake account
            </p>
        </div>
        """, unsafe_allow_html=True)

        with st.container():
            st.markdown("""
            <div style="background:#161b22; border:1px solid #30363d;
                 border-radius:14px; padding:32px 36px 28px;">
            """, unsafe_allow_html=True)

            account  = st.text_input("Account Identifier", placeholder="xy12345.us-east-1")
            username = st.text_input("Username", placeholder="your_username")
            password = st.text_input("Password", type="password", placeholder="••••••••")

            st.markdown("<br>", unsafe_allow_html=True)

            if st.button("Connect to Snowflake →"):
                if not account or not username or not password:
                    st.error("⚠️ Please fill in all fields.")
                else:
                    with st.spinner("Connecting to Snowflake..."):
                        try:
                            conn = sf_connect(account.strip(), username.strip(), password.strip())
                            st.session_state.sf_conn    = conn
                            st.session_state.sf_account = account.strip()
                            st.session_state.sf_user    = username.strip()

                            cur = conn.cursor()
                            cur.execute("SHOW WAREHOUSES")
                            st.session_state.warehouses = [r[0] for r in cur.fetchall()]

                            cur.execute("SHOW DATABASES")
                            st.session_state.databases = [r[1] for r in cur.fetchall()]
                            cur.close()

                            st.session_state.page = "setup"
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Connection failed: {e}")

            st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("""
        <p style="text-align:center; color:#484f58; font-family:'DM Mono',monospace;
           font-size:11px; margin-top:16px;">
            🔒 Credentials are not stored
        </p>
        """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
# PAGE 2 — SETUP
# ══════════════════════════════════════════════════════════════════════
def page_setup():
    h1, _, h3 = st.columns([4, 2, 1])
    with h1:
        st.markdown(f"""
        <h2 style="color:#e6edf3; margin:0;">❄️ Welcome, {st.session_state.sf_user}</h2>
        <p style="color:#8b949e; font-family:'DM Mono',monospace; font-size:12px; margin:4px 0 0;">
            {st.session_state.sf_account}
        </p>
        """, unsafe_allow_html=True)
    with h3:
        if st.button("Logout"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

    st.markdown("---")
    st.markdown("### 🗂️ Select your data source")
    st.markdown("<br>", unsafe_allow_html=True)

    # Step 1
    st.markdown('<div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:24px 28px;margin-bottom:16px;">', unsafe_allow_html=True)
    st.markdown('<p style="color:#29b6f6;font-family:DM Mono,monospace;font-size:11px;text-transform:uppercase;letter-spacing:2px;margin-bottom:16px;">01 · Compute & Database</p>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        wh = st.selectbox("Warehouse", options=st.session_state.warehouses, key="sel_wh")
    with col2:
        db = st.selectbox("Database", options=st.session_state.databases, key="sel_db")
    st.markdown('</div>', unsafe_allow_html=True)

    # Step 2
    st.markdown('<div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:24px 28px;margin-bottom:16px;">', unsafe_allow_html=True)
    st.markdown('<p style="color:#29b6f6;font-family:DM Mono,monospace;font-size:11px;text-transform:uppercase;letter-spacing:2px;margin-bottom:16px;">02 · Schema</p>', unsafe_allow_html=True)

    schema = None
    if db:
        if st.session_state.selected_db != db:
            st.session_state.selected_db    = db
            st.session_state.schemas        = []
            st.session_state.selected_schema = None
            st.session_state.tables         = []
            try:
                cur = st.session_state.sf_conn.cursor()
                cur.execute(f'SHOW SCHEMAS IN DATABASE "{db}"')
                st.session_state.schemas = [r[1] for r in cur.fetchall()]
                cur.close()
            except Exception as e:
                st.error(f"Error loading schemas: {e}")

        if st.session_state.schemas:
            schema = st.selectbox("Schema", options=st.session_state.schemas, key="sel_schema")
        else:
            st.info("No schemas found.")
    else:
        st.info("Select a database first.")
    st.markdown('</div>', unsafe_allow_html=True)

    # Step 3
    st.markdown('<div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:24px 28px;margin-bottom:16px;">', unsafe_allow_html=True)
    st.markdown('<p style="color:#29b6f6;font-family:DM Mono,monospace;font-size:11px;text-transform:uppercase;letter-spacing:2px;margin-bottom:16px;">03 · Table</p>', unsafe_allow_html=True)

    table = None
    if schema:
        if st.session_state.selected_schema != schema:
            st.session_state.selected_schema = schema
            st.session_state.tables          = []
            try:
                cur = st.session_state.sf_conn.cursor()
                cur.execute(f'SHOW TABLES IN "{db}"."{schema}"')
                st.session_state.tables = [r[1] for r in cur.fetchall()]
                cur.close()
            except Exception as e:
                st.error(f"Error loading tables: {e}")

        if st.session_state.tables:
            table = st.selectbox("Table", options=st.session_state.tables, key="sel_table")
        else:
            st.info("No tables found in this schema.")
    else:
        st.info("Select a schema first.")
    st.markdown('</div>', unsafe_allow_html=True)

    # Load
    if wh and db and schema and table:
        col_rpp, col_btn = st.columns([1, 2])
        with col_rpp:
            rpp = st.selectbox("Rows per page", [25, 50, 100, 200], index=1)
        with col_btn:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button(f"Load  {table}  →"):
                with st.spinner("Loading data..."):
                    try:
                        cur = st.session_state.sf_conn.cursor()
                        cur.execute(f'USE WAREHOUSE "{wh}"')
                        cur.close()

                        total = fetch_count(db, schema, table)
                        df    = fetch_table_page(db, schema, table, rpp, 0)

                        st.session_state.selected_wh     = wh
                        st.session_state.selected_db     = db
                        st.session_state.selected_schema = schema
                        st.session_state.selected_table  = table
                        st.session_state.rows_per_page   = rpp
                        st.session_state.total_rows      = total
                        st.session_state.df              = df
                        st.session_state.current_page    = 1
                        st.session_state.page            = "table"
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error loading table: {e}")


# ══════════════════════════════════════════════════════════════════════
# PAGE 3 — TABLE VIEWER
# ══════════════════════════════════════════════════════════════════════
def page_table():
    db     = st.session_state.selected_db
    schema = st.session_state.selected_schema
    table  = st.session_state.selected_table
    rpp    = st.session_state.rows_per_page
    total  = st.session_state.total_rows
    pg     = st.session_state.current_page
    total_pages = max(1, -(-total // rpp))
    start_row   = (pg - 1) * rpp + 1
    end_row     = min(pg * rpp, total)

    # Header
    h1, h2, h3 = st.columns([3, 2, 1])
    with h1:
        st.markdown(f"""
        <h2 style="color:#e6edf3; margin:0;">❄️ {table}</h2>
        <p style="color:#8b949e; font-family:'DM Mono',monospace; font-size:12px; margin:4px 0 0;">
            {db} · {schema}
        </p>
        """, unsafe_allow_html=True)
    with h2:
        st.markdown("""
        <div style="text-align:right; padding-top:10px;">
            <span style="background:rgba(16,185,129,0.1); border:1px solid rgba(16,185,129,0.3);
                  border-radius:20px; padding:4px 14px; color:#10b981;
                  font-family:'DM Mono',monospace; font-size:12px;">
                ● Live
            </span>
        </div>
        """, unsafe_allow_html=True)
    with h3:
        if st.button("← Back"):
            st.session_state.page = "setup"
            st.rerun()

    st.markdown("---")

    # Metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Rows",   f"{total:,}")
    m2.metric("Columns",      len(st.session_state.df.columns))
    m3.metric("Page",         f"{pg} / {total_pages}")
    m4.metric("Rows / Page",  rpp)

    st.markdown("<br>", unsafe_allow_html=True)

    # Table
    st.dataframe(
        st.session_state.df,
        use_container_width=True,
        height=580,
        hide_index=True,
    )

    # Pagination
    st.markdown("<br>", unsafe_allow_html=True)
    p1, p2, p3, p4, p5 = st.columns([1, 1, 2, 1, 1])

    with p1:
        if st.button("⟨⟨ First", disabled=(pg == 1), key="first"):
            st.session_state.current_page = 1
            st.session_state.df = fetch_table_page(db, schema, table, rpp, 0)
            st.rerun()
    with p2:
        if st.button("⟨ Prev", disabled=(pg == 1), key="prev"):
            st.session_state.current_page -= 1
            st.session_state.df = fetch_table_page(db, schema, table, rpp, (st.session_state.current_page - 1) * rpp)
            st.rerun()
    with p3:
        st.markdown(f"""
        <div style="text-align:center; padding:10px 0;
             font-family:'DM Mono',monospace; font-size:13px; color:#8b949e;">
            Rows <b style="color:#29b6f6">{start_row:,}</b> –
            <b style="color:#29b6f6">{end_row:,}</b>
            of <b style="color:#e6edf3">{total:,}</b>
        </div>
        """, unsafe_allow_html=True)
    with p4:
        if st.button("Next ⟩", disabled=(pg >= total_pages), key="next"):
            st.session_state.current_page += 1
            st.session_state.df = fetch_table_page(db, schema, table, rpp, (st.session_state.current_page - 1) * rpp)
            st.rerun()
    with p5:
        if st.button("Last ⟩⟩", disabled=(pg >= total_pages), key="last"):
            st.session_state.current_page = total_pages
            st.session_state.df = fetch_table_page(db, schema, table, rpp, (total_pages - 1) * rpp)
            st.rerun()

    # Jump to page
    st.markdown("<br>", unsafe_allow_html=True)
    j1, j2, _ = st.columns([1, 1, 4])
    with j1:
        jump = st.number_input("Jump to page", min_value=1, max_value=total_pages, value=pg, step=1)
    with j2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Go →", key="jump"):
            st.session_state.current_page = int(jump)
            st.session_state.df = fetch_table_page(db, schema, table, rpp, (int(jump) - 1) * rpp)
            st.rerun()


# ─── Router ────────────────────────────────────────────────────────────────────
page = st.session_state.page

if page == "login":
    page_login()
elif page == "setup":
    if st.session_state.sf_conn is None:
        st.session_state.page = "login"
        st.rerun()
    else:
        page_setup()
elif page == "table":
    if st.session_state.sf_conn is None:
        st.session_state.page = "login"
        st.rerun()
    else:
        page_table()
