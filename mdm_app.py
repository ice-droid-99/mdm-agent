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

# ─── Global CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@400;600;700;800&display=swap');

/* Reset & Base */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body, [data-testid="stAppViewContainer"] {
    background: #0a0e1a !important;
    color: #e2e8f0;
    font-family: 'Syne', sans-serif;
}

[data-testid="stAppViewContainer"] > .main {
    background: #0a0e1a !important;
}

/* Hide streamlit chrome */
#MainMenu, footer, header, [data-testid="stToolbar"] { display: none !important; }
[data-testid="stSidebarNav"] { display: none !important; }
.block-container { padding: 0 !important; max-width: 100% !important; }

/* ── LOGIN PAGE ─────────────────────────────────── */
.login-wrap {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #0a0e1a;
    position: relative;
    overflow: hidden;
}

.login-wrap::before {
    content: '';
    position: fixed;
    top: -40%;
    left: -20%;
    width: 80vw;
    height: 80vw;
    background: radial-gradient(circle, rgba(41,182,246,0.07) 0%, transparent 65%);
    pointer-events: none;
    z-index: 0;
}

.login-wrap::after {
    content: '';
    position: fixed;
    bottom: -30%;
    right: -10%;
    width: 60vw;
    height: 60vw;
    background: radial-gradient(circle, rgba(99,102,241,0.07) 0%, transparent 65%);
    pointer-events: none;
    z-index: 0;
}

.login-card {
    position: relative;
    z-index: 1;
    width: 440px;
    background: rgba(15,20,35,0.9);
    border: 1px solid rgba(41,182,246,0.15);
    border-radius: 20px;
    padding: 48px 44px 44px;
    backdrop-filter: blur(20px);
    box-shadow: 0 0 80px rgba(41,182,246,0.05), 0 40px 80px rgba(0,0,0,0.5);
}

.login-logo {
    font-size: 13px;
    font-family: 'DM Mono', monospace;
    color: #29b6f6;
    letter-spacing: 3px;
    text-transform: uppercase;
    margin-bottom: 10px;
}

.login-title {
    font-size: 32px;
    font-weight: 800;
    color: #f0f4ff;
    line-height: 1.15;
    margin-bottom: 6px;
}

.login-sub {
    font-size: 13px;
    color: #4a5568;
    font-family: 'DM Mono', monospace;
    margin-bottom: 36px;
}

/* Streamlit input overrides */
[data-testid="stTextInput"] label,
[data-testid="stSelectbox"] label {
    font-family: 'DM Mono', monospace !important;
    font-size: 11px !important;
    color: #4a6fa5 !important;
    text-transform: uppercase !important;
    letter-spacing: 1.5px !important;
    margin-bottom: 6px !important;
}

[data-testid="stTextInput"] input {
    background: rgba(255,255,255,0.03) !important;
    border: 1px solid rgba(41,182,246,0.2) !important;
    border-radius: 10px !important;
    color: #e2e8f0 !important;
    font-family: 'DM Mono', monospace !important;
    font-size: 14px !important;
    padding: 12px 16px !important;
    transition: border-color 0.2s !important;
}

[data-testid="stTextInput"] input:focus {
    border-color: rgba(41,182,246,0.6) !important;
    box-shadow: 0 0 0 3px rgba(41,182,246,0.08) !important;
    outline: none !important;
}

/* Buttons */
[data-testid="stButton"] > button {
    width: 100% !important;
    background: linear-gradient(135deg, #29b6f6 0%, #6366f1 100%) !important;
    color: #fff !important;
    border: none !important;
    border-radius: 10px !important;
    font-family: 'Syne', sans-serif !important;
    font-size: 15px !important;
    font-weight: 700 !important;
    letter-spacing: 0.5px !important;
    padding: 14px 24px !important;
    cursor: pointer !important;
    transition: opacity 0.2s, transform 0.15s !important;
    margin-top: 8px !important;
}

[data-testid="stButton"] > button:hover {
    opacity: 0.9 !important;
    transform: translateY(-1px) !important;
}

[data-testid="stButton"] > button:active {
    transform: translateY(0) !important;
}

/* ── SETUP / WELCOME PAGE ───────────────────────── */
.setup-wrap {
    min-height: 100vh;
    padding: 48px 60px;
    background: #0a0e1a;
}

.page-header {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 48px;
    padding-bottom: 24px;
    border-bottom: 1px solid rgba(41,182,246,0.1);
}

.page-badge {
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    color: #29b6f6;
    letter-spacing: 2px;
    text-transform: uppercase;
    background: rgba(41,182,246,0.08);
    border: 1px solid rgba(41,182,246,0.2);
    border-radius: 20px;
    padding: 4px 14px;
}

.page-title {
    font-size: 28px;
    font-weight: 800;
    color: #f0f4ff;
}

.page-user {
    margin-left: auto;
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: #4a5568;
}

.section-card {
    background: rgba(15,20,35,0.8);
    border: 1px solid rgba(41,182,246,0.1);
    border-radius: 16px;
    padding: 32px 36px;
    margin-bottom: 28px;
}

.section-label {
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    color: #29b6f6;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 20px;
}

/* Selectbox */
[data-testid="stSelectbox"] > div > div {
    background: rgba(255,255,255,0.03) !important;
    border: 1px solid rgba(41,182,246,0.2) !important;
    border-radius: 10px !important;
    color: #e2e8f0 !important;
    font-family: 'DM Mono', monospace !important;
    font-size: 14px !important;
}

/* ── DATA TABLE PAGE ────────────────────────────── */
.table-wrap {
    min-height: 100vh;
    padding: 32px 40px;
    background: #0a0e1a;
}

.table-header {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 28px;
}

.table-meta {
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: #4a5568;
}

.table-meta span {
    color: #29b6f6;
}

/* Dataframe styling */
[data-testid="stDataFrame"] {
    border: 1px solid rgba(41,182,246,0.15) !important;
    border-radius: 12px !important;
    overflow: hidden !important;
}

/* Pagination controls */
.pag-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 20px;
    padding: 16px 24px;
    background: rgba(15,20,35,0.8);
    border: 1px solid rgba(41,182,246,0.1);
    border-radius: 12px;
}

.pag-info {
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: #4a5568;
}

.pag-info b {
    color: #29b6f6;
}

/* Alert / error */
[data-testid="stAlert"] {
    border-radius: 10px !important;
    font-family: 'DM Mono', monospace !important;
    font-size: 13px !important;
}

/* Spinner */
[data-testid="stSpinner"] {
    color: #29b6f6 !important;
}

/* Divider */
hr { border-color: rgba(41,182,246,0.08) !important; }

/* Success box */
.success-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(16,185,129,0.08);
    border: 1px solid rgba(16,185,129,0.25);
    border-radius: 20px;
    padding: 4px 14px;
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: #10b981;
}

.nav-back {
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: #4a5568;
    cursor: pointer;
    margin-bottom: 24px;
    display: inline-block;
}

</style>
""", unsafe_allow_html=True)


# ─── Session State Defaults ────────────────────────────────────────────────────
for key, default in {
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
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ─── Snowflake Helpers ─────────────────────────────────────────────────────────
def sf_connect(account, user, password):
    return snowflake.connector.connect(
        account=account,
        user=user,
        password=password,
        login_timeout=15
    )

def sf_query(query):
    cur = st.session_state.sf_conn.cursor()
    cur.execute(query)
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description] if cur.description else []
    cur.close()
    return pd.DataFrame(rows, columns=cols)

def fetch_warehouses():
    return sf_query("SHOW WAREHOUSES")["name"].tolist()

def fetch_databases():
    return sf_query("SHOW DATABASES")["name"].tolist()

def fetch_schemas(db):
    return sf_query(f"SHOW SCHEMAS IN DATABASE {db}")["name"].tolist()

def fetch_tables(db, schema):
    return sf_query(f"SHOW TABLES IN {db}.{schema}")["name"].tolist()

def fetch_table_data(db, schema, table, limit, offset):
    df = sf_query(f"SELECT * FROM {db}.{schema}.{table} LIMIT {limit} OFFSET {offset}")
    return df

def fetch_row_count(db, schema, table):
    result = sf_query(f"SELECT COUNT(*) AS CNT FROM {db}.{schema}.{table}")
    return int(result["CNT"].iloc[0])


# ══════════════════════════════════════════════════════════════════════
# PAGE 1 — LOGIN
# ══════════════════════════════════════════════════════════════════════
def page_login():
    st.markdown("""
    <div class="login-wrap">
        <div class="login-card">
    """, unsafe_allow_html=True)

    col_center = st.columns([1, 2, 1])[1]
    with col_center:
        st.markdown('<div class="login-logo">❄ MDM Agent</div>', unsafe_allow_html=True)
        st.markdown('<div class="login-title">Connect to<br>Snowflake</div>', unsafe_allow_html=True)
        st.markdown('<div class="login-sub">// enter your credentials below</div>', unsafe_allow_html=True)

        account  = st.text_input("Account Identifier", placeholder="xy12345.us-east-1", key="inp_account")
        username = st.text_input("Username", placeholder="your_username", key="inp_user")
        password = st.text_input("Password", type="password", placeholder="••••••••••", key="inp_pass")

        st.markdown("<br>", unsafe_allow_html=True)

        if st.button("Connect to Snowflake →", key="btn_login"):
            if not account or not username or not password:
                st.error("Please fill in all fields.")
            else:
                with st.spinner("Connecting..."):
                    try:
                        conn = sf_connect(account.strip(), username.strip(), password.strip())
                        st.session_state.sf_conn    = conn
                        st.session_state.sf_account = account.strip()
                        st.session_state.sf_user    = username.strip()
                        st.session_state.warehouses = fetch_warehouses()
                        st.session_state.databases  = fetch_databases()
                        st.session_state.page       = "setup"
                        st.rerun()
                    except Exception as e:
                        st.error(f"Connection failed: {e}")

    st.markdown("</div></div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
# PAGE 2 — SETUP (WH / DB / SCHEMA / TABLE)
# ══════════════════════════════════════════════════════════════════════
def page_setup():
    st.markdown(f"""
    <div class="page-header">
        <div class="page-badge">❄ MDM Agent</div>
        <div class="page-title">Welcome, select your data source</div>
        <div class="page-user">
            <span style="color:#29b6f6">◉</span> &nbsp;{st.session_state.sf_user}@{st.session_state.sf_account}
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Step 1: Warehouse + Database ──────────────────────────────────
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-label">01 · Compute & Storage</div>', unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        wh = st.selectbox(
            "Warehouse",
            options=st.session_state.warehouses,
            index=0 if st.session_state.warehouses else None,
            key="sel_wh"
        )
    with col2:
        db = st.selectbox(
            "Database",
            options=st.session_state.databases,
            index=0 if st.session_state.databases else None,
            key="sel_db"
        )

    st.markdown('</div>', unsafe_allow_html=True)

    # ── Step 2: Schema ────────────────────────────────────────────────
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-label">02 · Schema</div>', unsafe_allow_html=True)

    if db:
        if st.session_state.selected_db != db:
            st.session_state.selected_db  = db
            st.session_state.schemas      = fetch_schemas(db)
            st.session_state.selected_schema = None
            st.session_state.tables       = []

        schema = st.selectbox(
            "Schema",
            options=st.session_state.schemas,
            index=0 if st.session_state.schemas else None,
            key="sel_schema"
        )
    else:
        schema = None
        st.info("Select a database first.")

    st.markdown('</div>', unsafe_allow_html=True)

    # ── Step 3: Table ─────────────────────────────────────────────────
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-label">03 · Table</div>', unsafe_allow_html=True)

    if schema:
        if st.session_state.selected_schema != schema:
            st.session_state.selected_schema = schema
            st.session_state.tables = fetch_tables(db, schema)

        table = st.selectbox(
            "Table",
            options=st.session_state.tables,
            index=0 if st.session_state.tables else None,
            key="sel_table"
        )
    else:
        table = None
        st.info("Select a schema first.")

    st.markdown('</div>', unsafe_allow_html=True)

    # ── Load Button ───────────────────────────────────────────────────
    if wh and db and schema and table:
        rpp = st.selectbox("Rows per page", [25, 50, 100, 200], index=1, key="sel_rpp")

        if st.button(f"Load  {db}.{schema}.{table}  →", key="btn_load"):
            with st.spinner("Fetching data..."):
                try:
                    # activate warehouse
                    cur = st.session_state.sf_conn.cursor()
                    cur.execute(f"USE WAREHOUSE {wh}")
                    cur.close()

                    total = fetch_row_count(db, schema, table)
                    df    = fetch_table_data(db, schema, table, rpp, 0)

                    st.session_state.selected_wh     = wh
                    st.session_state.selected_db     = db
                    st.session_state.selected_schema = schema
                    st.session_state.selected_table  = table
                    st.session_state.df              = df
                    st.session_state.total_rows      = total
                    st.session_state.rows_per_page   = rpp
                    st.session_state.current_page    = 1
                    st.session_state.page            = "table"
                    st.rerun()
                except Exception as e:
                    st.error(f"Error loading table: {e}")

    # Logout
    st.markdown("<br><br>", unsafe_allow_html=True)
    if st.button("← Disconnect", key="btn_logout"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()


# ══════════════════════════════════════════════════════════════════════
# PAGE 3 — TABLE VIEWER WITH PAGINATION
# ══════════════════════════════════════════════════════════════════════
def page_table():
    db     = st.session_state.selected_db
    schema = st.session_state.selected_schema
    table  = st.session_state.selected_table
    wh     = st.session_state.selected_wh
    rpp    = st.session_state.rows_per_page
    total  = st.session_state.total_rows
    pg     = st.session_state.current_page
    total_pages = max(1, -(-total // rpp))  # ceiling division

    # ── Header ────────────────────────────────────────────────────────
    st.markdown(f"""
    <div class="page-header">
        <div class="page-badge">❄ MDM Agent</div>
        <div class="page-title">{table}</div>
        <div class="page-user" style="display:flex; align-items:center; gap:12px;">
            <span class="success-pill">● Connected</span>
            <span style="color:#4a5568; font-family:'DM Mono',monospace; font-size:12px">
                {db} · {schema} · {wh}
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Stats Row ─────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    for col, label, val in [
        (m1, "Total Rows",   f"{total:,}"),
        (m2, "Columns",      str(len(st.session_state.df.columns))),
        (m3, "Page",         f"{pg} / {total_pages}"),
        (m4, "Rows / Page",  str(rpp)),
    ]:
        col.markdown(f"""
        <div style="background:rgba(15,20,35,0.8); border:1px solid rgba(41,182,246,0.1);
             border-radius:12px; padding:18px 20px;">
            <div style="font-family:'DM Mono',monospace; font-size:10px; color:#4a5568;
                 text-transform:uppercase; letter-spacing:1.5px; margin-bottom:6px">{label}</div>
            <div style="font-size:24px; font-weight:800; color:#29b6f6">{val}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Dataframe ─────────────────────────────────────────────────────
    st.dataframe(
        st.session_state.df,
        use_container_width=True,
        height=620,
        hide_index=True,
    )

    # ── Pagination Controls ───────────────────────────────────────────
    start_row = (pg - 1) * rpp + 1
    end_row   = min(pg * rpp, total)

    pcol1, pcol2, pcol3, pcol4, pcol5 = st.columns([1.2, 1, 2, 1, 1.2])

    with pcol1:
        if st.button("⟨⟨  First", key="btn_first", disabled=(pg == 1)):
            st.session_state.current_page = 1
            _load_page(db, schema, table, rpp, 0)

    with pcol2:
        if st.button("⟨  Prev", key="btn_prev", disabled=(pg == 1)):
            st.session_state.current_page -= 1
            _load_page(db, schema, table, rpp, (st.session_state.current_page - 1) * rpp)

    with pcol3:
        st.markdown(f"""
        <div style="text-align:center; font-family:'DM Mono',monospace; font-size:12px;
             color:#4a5568; padding:10px 0;">
            Showing rows <b style="color:#29b6f6">{start_row:,}</b> –
            <b style="color:#29b6f6">{end_row:,}</b>
            of <b style="color:#e2e8f0">{total:,}</b>
        </div>
        """, unsafe_allow_html=True)

    with pcol4:
        if st.button("Next  ⟩", key="btn_next", disabled=(pg >= total_pages)):
            st.session_state.current_page += 1
            _load_page(db, schema, table, rpp, (st.session_state.current_page - 1) * rpp)

    with pcol5:
        if st.button("Last  ⟩⟩", key="btn_last", disabled=(pg >= total_pages)):
            st.session_state.current_page = total_pages
            _load_page(db, schema, table, rpp, (total_pages - 1) * rpp)

    # ── Jump to page ──────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    jcol1, jcol2, jcol3 = st.columns([1, 1, 3])
    with jcol1:
        jump = st.number_input(
            "Jump to page", min_value=1, max_value=total_pages,
            value=pg, step=1, key="inp_jump"
        )
    with jcol2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Go →", key="btn_jump"):
            st.session_state.current_page = int(jump)
            _load_page(db, schema, table, rpp, (int(jump) - 1) * rpp)

    # ── Back button ───────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    bcol1, _ = st.columns([1, 5])
    with bcol1:
        if st.button("← Back to Setup", key="btn_back"):
            st.session_state.page = "setup"
            st.rerun()


def _load_page(db, schema, table, rpp, offset):
    with st.spinner("Loading..."):
        st.session_state.df = fetch_table_data(db, schema, table, rpp, offset)
    st.rerun()


# ─── Router ────────────────────────────────────────────────────────────────────
p = st.session_state.page

if p == "login":
    page_login()
elif p == "setup":
    if st.session_state.sf_conn is None:
        st.session_state.page = "login"
        st.rerun()
    else:
        page_setup()
elif p == "table":
    if st.session_state.sf_conn is None:
        st.session_state.page = "login"
        st.rerun()
    else:
        page_table()