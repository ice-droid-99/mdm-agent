import streamlit as st
import snowflake.connector
import pandas as pd
import google.generativeai as genai
import json
import re
import uuid
from datetime import datetime
from itertools import combinations
from collections import defaultdict

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="MDM Agent", page_icon="❄️", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@600;700;800&display=swap');
html,body,[data-testid="stAppViewContainer"]{background:#0a0d14 !important;}
[data-testid="stAppViewContainer"]>.main{background:#0a0d14 !important;}
[data-testid="stHeader"]{background:transparent !important;}
section[data-testid="stSidebar"]{display:none !important;}
#MainMenu,footer{visibility:hidden;}
*{font-family:'Syne',sans-serif;}
code,pre,[data-testid="stTextInput"] input,[data-testid="stSelectbox"] *{font-family:'DM Mono',monospace !important;}
[data-testid="stTextInput"] input{background:#111827 !important;border:1px solid #1f2937 !important;border-radius:8px !important;color:#f9fafb !important;font-size:13px !important;}
[data-testid="stTextInput"] input:focus{border-color:#3b82f6 !important;box-shadow:0 0 0 3px rgba(59,130,246,0.15) !important;}
[data-testid="stTextInput"] label,[data-testid="stSelectbox"] label{color:#6b7280 !important;font-family:'DM Mono',monospace !important;font-size:11px !important;text-transform:uppercase !important;letter-spacing:1.5px !important;}
[data-testid="stSelectbox"]>div>div{background:#111827 !important;border:1px solid #1f2937 !important;border-radius:8px !important;color:#f9fafb !important;}
[data-testid="stButton"]>button{background:linear-gradient(135deg,#3b82f6,#6366f1) !important;color:#fff !important;border:none !important;border-radius:8px !important;font-weight:700 !important;font-size:13px !important;width:100% !important;padding:12px !important;letter-spacing:0.5px !important;transition:opacity .2s,transform .1s !important;}
[data-testid="stButton"]>button:hover{opacity:.88 !important;transform:translateY(-1px) !important;}
[data-testid="stMetric"]{background:#111827;border:1px solid #1f2937;border-radius:12px;padding:20px !important;}
[data-testid="stMetricValue"]{color:#3b82f6 !important;font-size:26px !important;font-weight:800 !important;}
[data-testid="stMetricLabel"]{color:#6b7280 !important;font-size:11px !important;}
[data-testid="stDataFrame"]{border:1px solid #1f2937 !important;border-radius:10px !important;}
hr{border-color:#1f2937 !important;}
.stTabs [data-baseweb="tab-list"]{background:#111827;border-radius:10px;gap:4px;padding:4px;}
.stTabs [data-baseweb="tab"]{border-radius:8px !important;color:#6b7280 !important;}
.stTabs [aria-selected="true"]{background:#1f2937 !important;color:#f9fafb !important;}
</style>
""", unsafe_allow_html=True)

# ── Session defaults ───────────────────────────────────────────────────────────
DEFAULTS = {
    "page":"login","sf_conn":None,"sf_account":"","sf_user":"",
    "warehouses":[],"databases":[],"schemas":[],"tables":[],
    "sel_db":None,"sel_schema":None,"sel_table":None,"sel_wh":None,
    "df":None,"total_rows":0,"current_page":1,"rows_per_page":50,
    "table_schema":None,   # Claude's understanding of the table
    "blocking_keys":None,  # Claude-defined blocking strategy
    "candidates":[],
    "analysis_done":False,
    "decisions":{},
    "gemini_key":"",
}
for k,v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Snowflake ──────────────────────────────────────────────────────────────────
def sf_query(sql):
    cur = st.session_state.sf_conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description] if cur.description else []
    cur.close()
    return pd.DataFrame(rows, columns=cols)

def sf_write(sql):
    cur = st.session_state.sf_conn.cursor()
    cur.execute(sql)
    st.session_state.sf_conn.commit()
    cur.close()

def get_page(limit, offset):
    db,sc,tb = st.session_state.sel_db,st.session_state.sel_schema,st.session_state.sel_table
    return sf_query(f'SELECT * FROM "{db}"."{sc}"."{tb}" LIMIT {limit} OFFSET {offset}')

def get_count():
    db,sc,tb = st.session_state.sel_db,st.session_state.sel_schema,st.session_state.sel_table
    r = sf_query(f'SELECT COUNT(*) AS C FROM "{db}"."{sc}"."{tb}"')
    return int(r["C"].iloc[0])

def gemini(prompt, max_tokens=4000):
    """Call Gemini and return text response."""
    genai.configure(api_key=st.session_state.gemini_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-preview-04-17",
        generation_config=genai.GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=0.1,
        )
    )
    resp = model.generate_content(prompt)
    return resp.text.strip()

def claude_json(prompt, max_tokens=4000):
    """Call Gemini, expect JSON back, robust parsing."""
    text = gemini(prompt, max_tokens)
    text = re.sub(r"```json|```", "", text).strip()
    try:
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if m:
            return json.loads(m.group(1))
    except json.JSONDecodeError:
        pass
    try:
        fixed = text
        fixed += "]" * max(0, text.count("[") - text.count("]"))
        fixed += "}" * max(0, text.count("{") - text.count("}"))
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", fixed)
        if m:
            return json.loads(m.group(1))
    except Exception:
        pass
    return {"id_column":"","column_types":{},"blocking_columns":[],"notes":"Parse error - please retry."}


# ══════════════════════════════════════════════════════════════════════
# STEP A — Claude understands the table
# ══════════════════════════════════════════════════════════════════════
def claude_understand_table(df):
    """
    Give Claude column names + 5 sample rows.
    Claude tells us: which columns are IDs, names, emails,
    phones, dates, addresses, and what blocking groups to use.
    Returns a dict with full schema understanding.
    """
    cols = list(df.columns)
    sample = df.head(5).to_dict(orient="records")

    prompt = f"""You are a master data management expert analyzing a customer table.

Column names: {cols}

Sample rows (first 5):
{json.dumps(sample, default=str, indent=2)}

Your job is to understand this table's structure completely.

Return ONLY a JSON object in this exact format:
{{
  "id_column": "<the primary key / unique ID column name>",
  "column_types": {{
    "<col_name>": "<type>"
  }},
  "blocking_columns": ["<col1>", "<col2>", ...],
  "notes": "<any observations about data quality, mixed formats etc>"
}}

For column_types, use these type labels:
- "id" for primary keys / unique identifiers
- "name" for any name fields (first, last, full, abbreviated)
- "email" for email addresses
- "phone" for phone/mobile/contact numbers
- "date" for dates (DOB, registration date etc) — note if mixed formats
- "address" for address, city, street, location fields
- "gender" for gender/sex fields
- "numeric_id" for CIF, account numbers, numeric codes
- "ip" for IP addresses
- "other" for anything else

For blocking_columns: pick 3-5 columns that would be most useful to group records by for duplicate detection. These should be columns where two records sharing the same value is a strong signal they might be duplicates (email, phone, CIF, etc).

Be smart — infer column meaning from the name AND the sample data, not just the column name."""

    return claude_json(prompt, max_tokens=1500)

# ══════════════════════════════════════════════════════════════════════
# STEP B — Claude builds blocking groups
# ══════════════════════════════════════════════════════════════════════
def build_candidate_pairs(all_records, schema_info):
    """
    Use Claude-identified blocking columns to group records.
    Return candidate pairs (index_a, index_b) to score.
    Python just does set grouping — no hardcoded logic.
    """
    blocking_cols = schema_info.get("blocking_columns", [])
    id_col        = schema_info.get("id_column", all_records[0].keys().__iter__().__next__()
                                    if all_records else "id")

    blocks = defaultdict(set)

    for i, rec in enumerate(all_records):
        for col in blocking_cols:
            val = str(rec.get(col, "") or "").strip().lower()
            if val and val not in ("none","null","nan",""):
                # normalize minimally — just strip spaces and lowercase
                # Claude will do the deep normalization during scoring
                val_clean = re.sub(r"\s+","",val)
                if len(val_clean) >= 3:
                    blocks[f"{col}::{val_clean}"].add(i)

    pairs = set()
    for grp in blocks.values():
        grp = list(grp)
        if len(grp) < 2: continue
        for a, b in combinations(sorted(grp), 2):
            pairs.add((min(a,b), max(a,b)))

    return list(pairs)

# ══════════════════════════════════════════════════════════════════════
# STEP C — Claude scores each pair
# ══════════════════════════════════════════════════════════════════════
def score_pair(rec_a, rec_b, schema_info):
    """
    Give Claude both raw records + its own schema understanding.
    Claude normalizes, compares, and decides — fully autonomous.
    """
    prompt = f"""You are a master data management expert.

You previously analyzed this table and found these column types:
{json.dumps(schema_info.get("column_types", {}), indent=2)}

Now compare these two customer records and decide if they are the SAME real person.

Record A:
{json.dumps(rec_a, default=str, indent=2)}

Record B:
{json.dumps(rec_b, default=str, indent=2)}

Instructions:
- You understand what each column means from the schema above
- Normalize values yourself before comparing:
  * Dates: treat "15-03-1990", "1990-03-15", "15031990", "March 15 1990" as identical
  * Names: treat "M. Ali", "Mohd Ali", "Mohammed Ali" as the same
  * Phones: ignore +91, spaces, dashes — compare last 10 digits
  * Addresses: ignore case, punctuation, abbreviations (St vs Street, Rd vs Road)
  * Email: case insensitive
- Same numeric ID (CIF, account) = very strong duplicate signal
- Same email = very strong signal
- Same phone = strong signal
- Same IP = moderate signal
- Name similarity alone = weak signal, needs other corroboration
- Different city is NOT enough to say different person

Return ONLY this JSON, nothing else:
{{
  "decision": "DUPLICATE" or "NOT_DUPLICATE" or "NEEDS_REVIEW",
  "confidence": <0-100>,
  "matched_on": "<which fields matched, comma separated>",
  "reason": "<one clear sentence explaining the decision>",
  "surviving_record": "A" or "B"
}}

surviving_record should be whichever record is more complete and accurate."""

    try:
        result = claude_json(prompt, max_tokens=400)
        return result
    except Exception as e:
        return {
            "decision":"NEEDS_REVIEW","confidence":0,
            "matched_on":"error","reason":str(e),"surviving_record":"A"
        }

# ══════════════════════════════════════════════════════════════════════
# UI HELPERS
# ══════════════════════════════════════════════════════════════════════
def topbar(title, subtitle=""):
    h1, h2, h3 = st.columns([4,2,1])
    with h1:
        st.markdown(f'<h2 style="color:#f9fafb;margin:0;font-size:22px;">❄️ {title}</h2>', unsafe_allow_html=True)
        if subtitle:
            st.markdown(f'<p style="color:#6b7280;font-family:DM Mono,monospace;font-size:11px;margin:4px 0 0;">{subtitle}</p>', unsafe_allow_html=True)
    with h2:
        steps  = ["login","setup","table","agent","hitl","golden"]
        labels = ["Login","Setup","View","Agent","Review","Golden"]
        cur    = st.session_state.page
        idx    = steps.index(cur) if cur in steps else 0
        html   = '<div style="display:flex;gap:4px;align-items:center;padding-top:8px;">'
        for i,lbl in enumerate(labels):
            bg = "#3b82f6" if i==idx else ("#10b981" if i<idx else "#1f2937")
            fc = "#f9fafb" if i<=idx else "#4b5563"
            html += f'<div style="background:{bg};border-radius:4px;padding:3px 8px;font-family:DM Mono,monospace;font-size:9px;color:{fc};white-space:nowrap;">{lbl}</div>'
        html += '</div>'
        st.markdown(html, unsafe_allow_html=True)
    with h3:
        if st.button("Logout", key="logout_btn"):
            for k in list(st.session_state.keys()): del st.session_state[k]
            st.rerun()

def card(label):
    return f'<div style="background:#111827;border:1px solid #1f2937;border-radius:12px;padding:24px 28px;margin-bottom:16px;"><p style="color:#3b82f6;font-family:DM Mono,monospace;font-size:11px;text-transform:uppercase;letter-spacing:2px;margin-bottom:16px;">{label}</p>'

def step_msg(label, msg):
    return f'<p style="font-family:DM Mono,monospace;font-size:13px;color:#9ca3af;"><span style="color:#3b82f6;font-weight:700;">{label}</span> · {msg}</p>'

# ══════════════════════════════════════════════════════════════════════
# PAGE 1 — LOGIN
# ══════════════════════════════════════════════════════════════════════
def page_login():
    st.markdown("<br><br>", unsafe_allow_html=True)
    _, c, _ = st.columns([1,1.1,1])
    with c:
        st.markdown("""
        <div style="text-align:center;margin-bottom:32px;">
            <div style="font-size:56px;margin-bottom:10px;">❄️</div>
            <div style="font-size:28px;font-weight:800;color:#f9fafb;">MDM Agent</div>
            <div style="font-family:'DM Mono',monospace;font-size:12px;color:#4b5563;
                 margin-top:6px;letter-spacing:2px;">MASTER DATA MANAGEMENT · AI POWERED</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div style="background:#111827;border:1px solid #1f2937;border-radius:14px;padding:32px 36px;">', unsafe_allow_html=True)
        account  = st.text_input("Snowflake Account",  placeholder="xy12345.us-east-1")
        username = st.text_input("Username",            placeholder="your_username")
        password = st.text_input("Password", type="password", placeholder="••••••••")
        api_key  = st.text_input("Gemini API Key",  type="password", placeholder="AIza...")
        st.markdown("<br>", unsafe_allow_html=True)

        if st.button("Connect & Start →"):
            if not all([account, username, password, api_key]):
                st.error("Please fill all fields.")
            else:
                with st.spinner("Connecting to Snowflake..."):
                    try:
                        conn = snowflake.connector.connect(
                            account=account.strip(), user=username.strip(),
                            password=password.strip(), login_timeout=15)
                        st.session_state.sf_conn       = conn
                        st.session_state.sf_account    = account.strip()
                        st.session_state.sf_user       = username.strip()
                        st.session_state.gemini_key = api_key.strip()
                        cur = conn.cursor()
                        cur.execute("SHOW WAREHOUSES")
                        st.session_state.warehouses = [r[0] for r in cur.fetchall()]
                        cur.execute("SHOW DATABASES")
                        st.session_state.databases  = [r[1] for r in cur.fetchall()]
                        cur.close()
                        st.session_state.page = "setup"
                        st.rerun()
                    except Exception as e:
                        st.error(f"Connection failed: {e}")
        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown('<p style="text-align:center;color:#374151;font-family:DM Mono,monospace;font-size:11px;margin-top:14px;">🔒 Nothing stored · Session only</p>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════
# PAGE 2 — SETUP
# ══════════════════════════════════════════════════════════════════════
def page_setup():
    topbar("Select Data Source")
    st.markdown("---")

    st.markdown(card("01 · Warehouse & Database"), unsafe_allow_html=True)
    c1,c2 = st.columns(2)
    with c1: wh = st.selectbox("Warehouse", st.session_state.warehouses, key="wh_s")
    with c2: db = st.selectbox("Database",  st.session_state.databases,  key="db_s")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(card("02 · Schema"), unsafe_allow_html=True)
    schema = None
    if db:
        if st.session_state.sel_db != db:
            st.session_state.sel_db = db
            st.session_state.schemas = []
            st.session_state.sel_schema = None
            try:
                cur = st.session_state.sf_conn.cursor()
                cur.execute(f'SHOW SCHEMAS IN DATABASE "{db}"')
                st.session_state.schemas = [r[1] for r in cur.fetchall()]
                cur.close()
            except Exception as e: st.error(str(e))
        if st.session_state.schemas:
            schema = st.selectbox("Schema", st.session_state.schemas, key="sc_s")
        else: st.info("No schemas found.")
    else: st.info("Select a database first.")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(card("03 · Table"), unsafe_allow_html=True)
    table = None
    if schema:
        if st.session_state.sel_schema != schema:
            st.session_state.sel_schema = schema
            st.session_state.tables = []
            try:
                cur = st.session_state.sf_conn.cursor()
                cur.execute(f'SHOW TABLES IN "{db}"."{schema}"')
                st.session_state.tables = [r[1] for r in cur.fetchall()]
                cur.close()
            except Exception as e: st.error(str(e))
        if st.session_state.tables:
            table = st.selectbox("Table", st.session_state.tables, key="tb_s")
        else: st.info("No tables found.")
    else: st.info("Select a schema first.")
    st.markdown("</div>", unsafe_allow_html=True)

    if wh and db and schema and table:
        ca,cb = st.columns([1,2])
        with ca: rpp = st.selectbox("Rows / page", [25,50,100,200], index=1)
        with cb:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button(f"Load  {table}  →"):
                with st.spinner("Loading..."):
                    try:
                        cur = st.session_state.sf_conn.cursor()
                        cur.execute(f'USE WAREHOUSE "{wh}"')
                        cur.close()
                        st.session_state.sel_wh        = wh
                        st.session_state.sel_db        = db
                        st.session_state.sel_schema    = schema
                        st.session_state.sel_table     = table
                        st.session_state.rows_per_page = rpp
                        st.session_state.total_rows    = get_count()
                        st.session_state.df            = get_page(rpp, 0)
                        st.session_state.current_page  = 1
                        st.session_state.analysis_done = False
                        st.session_state.candidates    = []
                        st.session_state.decisions     = {}
                        st.session_state.table_schema  = None
                        st.session_state.page = "table"
                        st.rerun()
                    except Exception as e: st.error(str(e))

# ══════════════════════════════════════════════════════════════════════
# PAGE 3 — TABLE VIEWER
# ══════════════════════════════════════════════════════════════════════
def page_table():
    db,sc,tb = st.session_state.sel_db,st.session_state.sel_schema,st.session_state.sel_table
    rpp      = st.session_state.rows_per_page
    total    = st.session_state.total_rows
    pg       = st.session_state.current_page
    tp       = max(1,-(-total//rpp))
    s,e      = (pg-1)*rpp+1, min(pg*rpp,total)

    topbar(f"{tb}", subtitle=f"{db} · {sc}")
    st.markdown("---")

    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Total Rows", f"{total:,}")
    m2.metric("Columns",    len(st.session_state.df.columns))
    m3.metric("Page",       f"{pg}/{tp}")
    m4.metric("Rows/Page",  rpp)
    st.markdown("<br>", unsafe_allow_html=True)

    st.dataframe(st.session_state.df, use_container_width=True, height=500, hide_index=True)

    p1,p2,p3,p4,p5 = st.columns([1,1,2,1,1])
    with p1:
        if st.button("⟨⟨ First", disabled=(pg==1), key="bf"):
            st.session_state.current_page=1
            st.session_state.df=get_page(rpp,0); st.rerun()
    with p2:
        if st.button("⟨ Prev", disabled=(pg==1), key="bp"):
            st.session_state.current_page-=1
            st.session_state.df=get_page(rpp,(st.session_state.current_page-1)*rpp); st.rerun()
    with p3:
        st.markdown(f'<div style="text-align:center;padding:10px 0;font-family:DM Mono,monospace;font-size:12px;color:#6b7280;">Rows <b style="color:#3b82f6">{s:,}</b>–<b style="color:#3b82f6">{e:,}</b> of <b style="color:#f9fafb">{total:,}</b></div>', unsafe_allow_html=True)
    with p4:
        if st.button("Next ⟩",  disabled=(pg>=tp), key="bn"):
            st.session_state.current_page+=1
            st.session_state.df=get_page(rpp,(st.session_state.current_page-1)*rpp); st.rerun()
    with p5:
        if st.button("Last ⟩⟩", disabled=(pg>=tp), key="bl"):
            st.session_state.current_page=tp
            st.session_state.df=get_page(rpp,(tp-1)*rpp); st.rerun()

    j1,j2,_ = st.columns([1,1,4])
    with j1: jump = st.number_input("Jump to page",1,tp,pg,1)
    with j2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Go →", key="bj"):
            st.session_state.current_page=int(jump)
            st.session_state.df=get_page(rpp,(int(jump)-1)*rpp); st.rerun()

    st.markdown("---")
    st.markdown(card("🤖 MDM Duplicate Detection Agent"), unsafe_allow_html=True)
    st.markdown('<p style="color:#9ca3af;font-size:13px;margin-bottom:8px;">The agent will first <b style="color:#f9fafb">read your table structure</b> and understand what each column means — no matter what the column names are. Then it will find all duplicate customers across the entire table.</p>', unsafe_allow_html=True)
    st.markdown('<p style="color:#6b7280;font-family:DM Mono,monospace;font-size:11px;margin-bottom:16px;">Works on any table · any column names · any date formats · any combinations</p>', unsafe_allow_html=True)
    if st.button("🔍 Run MDM Agent →"):
        st.session_state.page = "agent"; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════
# PAGE 4 — AGENT
# ══════════════════════════════════════════════════════════════════════
def page_agent():
    topbar("MDM Agent Running", subtitle="Claude is analyzing your table")
    st.markdown("---")

    db,sc,tb = st.session_state.sel_db,st.session_state.sel_schema,st.session_state.sel_table

    if not st.session_state.analysis_done:
        st.markdown('<div style="background:#111827;border:1px solid #1f2937;border-radius:14px;padding:32px;">', unsafe_allow_html=True)
        status  = st.empty()
        bar     = st.progress(0)
        detail  = st.empty()
        insight = st.empty()

        # ── Step 1: Fetch ALL records ─────────────────────────────────
        status.markdown(step_msg("Step 1/4","Fetching all records from Snowflake..."))
        bar.progress(5)
        all_df   = sf_query(f'SELECT * FROM "{db}"."{sc}"."{tb}"')
        cols     = list(all_df.columns)
        all_recs = all_df.to_dict(orient="records")
        detail.markdown(f'<p style="color:#6b7280;font-family:DM Mono,monospace;font-size:12px;">✓ Fetched {len(all_recs):,} records · {len(cols)} columns detected: <span style="color:#3b82f6">{", ".join(cols)}</span></p>', unsafe_allow_html=True)

        # ── Step 2: Claude understands the table ─────────────────────
        status.markdown(step_msg("Step 2/4","Claude is reading and understanding your table structure..."))
        bar.progress(20)

        schema_info = claude_understand_table(all_df)
        st.session_state.table_schema = schema_info

        id_col = schema_info.get("id_column", cols[0])
        col_types = schema_info.get("column_types", {})
        blocking_cols = schema_info.get("blocking_columns", [])
        notes = schema_info.get("notes", "")

        insight.markdown(f"""
        <div style="background:#0d1117;border:1px solid #1f2937;border-radius:10px;padding:16px;margin-top:12px;">
            <p style="color:#3b82f6;font-family:DM Mono,monospace;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;">🧠 Claude's Table Understanding</p>
            <p style="color:#9ca3af;font-family:DM Mono,monospace;font-size:12px;margin-bottom:6px;"><b style="color:#f9fafb;">ID Column:</b> {id_col}</p>
            <p style="color:#9ca3af;font-family:DM Mono,monospace;font-size:12px;margin-bottom:6px;"><b style="color:#f9fafb;">Column Types:</b> {", ".join([f"{k}={v}" for k,v in col_types.items()])}</p>
            <p style="color:#9ca3af;font-family:DM Mono,monospace;font-size:12px;margin-bottom:6px;"><b style="color:#f9fafb;">Blocking On:</b> {", ".join(blocking_cols)}</p>
            <p style="color:#9ca3af;font-family:DM Mono,monospace;font-size:12px;"><b style="color:#f9fafb;">Observations:</b> {notes}</p>
        </div>
        """, unsafe_allow_html=True)

        # ── Step 3: Build candidate pairs ────────────────────────────
        status.markdown(step_msg("Step 3/4","Building candidate pairs using Claude-defined blocking..."))
        bar.progress(40)

        pairs = build_candidate_pairs(all_recs, schema_info)
        detail.markdown(f'<p style="color:#6b7280;font-family:DM Mono,monospace;font-size:12px;">✓ Found <b style="color:#3b82f6">{len(pairs)}</b> candidate pairs to score (skipped {len(all_recs)*(len(all_recs)-1)//2 - len(pairs):,} non-candidate combinations)</p>', unsafe_allow_html=True)

        # ── Step 4: Claude scores every pair ─────────────────────────
        status.markdown(step_msg("Step 4/4",f"Claude scoring {len(pairs)} pairs — normalizing and comparing..."))
        candidates = []

        for i,(ia,ib) in enumerate(pairs):
            pct = 40 + int((i/max(len(pairs),1))*58)
            bar.progress(min(pct,98))

            ra = all_recs[ia]
            rb = all_recs[ib]
            id_a = str(ra.get(id_col, f"row_{ia}"))
            id_b = str(rb.get(id_col, f"row_{ib}"))

            detail.markdown(f'<p style="color:#6b7280;font-family:DM Mono,monospace;font-size:12px;">Scoring {i+1}/{len(pairs)} · <span style="color:#3b82f6">{id_a}</span> vs <span style="color:#6366f1">{id_b}</span></p>', unsafe_allow_html=True)

            result = score_pair(ra, rb, schema_info)

            if result.get("decision") in ("DUPLICATE","NEEDS_REVIEW"):
                candidates.append({
                    "pair_id":    str(uuid.uuid4())[:8].upper(),
                    "id_a":       id_a,
                    "id_b":       id_b,
                    "rec_a":      ra,
                    "rec_b":      rb,
                    "decision":   result.get("decision","NEEDS_REVIEW"),
                    "confidence": int(result.get("confidence",0)),
                    "matched_on": result.get("matched_on",""),
                    "reason":     result.get("reason",""),
                    "surviving":  result.get("surviving_record","A"),
                    "status":     "PENDING"
                })

        bar.progress(100)
        status.markdown(step_msg("✅ Complete", f"Found {len(candidates)} duplicate/review candidates"))
        st.session_state.candidates    = candidates
        st.session_state.analysis_done = True
        st.session_state.decisions     = {c["pair_id"]:"PENDING" for c in candidates}
        st.markdown("</div>", unsafe_allow_html=True)
        st.rerun()

    else:
        cands = st.session_state.candidates
        dups  = [c for c in cands if c["decision"]=="DUPLICATE"]
        revs  = [c for c in cands if c["decision"]=="NEEDS_REVIEW"]

        st.success(f"✅ Analysis complete — **{len(cands)}** candidate pairs found")

        # Show schema insight
        si = st.session_state.table_schema
        if si:
            with st.expander("🧠 Claude's Table Understanding", expanded=False):
                st.json(si)

        m1,m2,m3 = st.columns(3)
        m1.metric("Duplicates",   len(dups))
        m2.metric("Needs Review", len(revs))
        m3.metric("Total Scored", len(cands))

        st.markdown("<br>", unsafe_allow_html=True)
        c1,c2 = st.columns(2)
        with c1:
            if st.button("👤 Go to Human Review →"):
                st.session_state.page="hitl"; st.rerun()
        with c2:
            if st.button("🔄 Re-run Analysis"):
                st.session_state.analysis_done=False
                st.session_state.candidates=[]
                st.session_state.decisions={}
                st.rerun()

# ══════════════════════════════════════════════════════════════════════
# PAGE 5 — HITL REVIEW
# ══════════════════════════════════════════════════════════════════════
def page_hitl():
    topbar("Human Review", subtitle="Approve or reject each duplicate pair")
    st.markdown("---")

    cands     = st.session_state.candidates
    decisions = st.session_state.decisions

    if not cands:
        st.info("No candidates. Run the agent first.")
        if st.button("← Back"): st.session_state.page="table"; st.rerun()
        return

    approved = sum(1 for v in decisions.values() if v=="APPROVED")
    rejected = sum(1 for v in decisions.values() if v=="REJECTED")
    pending  = sum(1 for v in decisions.values() if v=="PENDING")

    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Total Pairs", len(cands))
    m2.metric("✅ Approved",  approved)
    m3.metric("❌ Rejected",  rejected)
    m4.metric("⏳ Pending",   pending)

    # Bulk actions
    st.markdown("<br>", unsafe_allow_html=True)
    ba,bb,bc = st.columns(3)
    with ba:
        thresh = st.slider("Bulk approve threshold", 70, 100, 90)
    with bb:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button(f"✅ Approve all ≥ {thresh}%"):
            for c in cands:
                if c["confidence"] >= thresh:
                    st.session_state.decisions[c["pair_id"]] = "APPROVED"
            st.rerun()
    with bc:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("❌ Reject all < 70%"):
            for c in cands:
                if c["confidence"] < 70:
                    st.session_state.decisions[c["pair_id"]] = "REJECTED"
            st.rerun()

    st.markdown("---")

    tab1,tab2,tab3 = st.tabs([
        f"⏳ Pending ({pending})",
        f"✅ Approved ({approved})",
        f"❌ Rejected ({rejected})"
    ])

    def render_pairs(filter_status):
        shown = [c for c in cands if decisions.get(c["pair_id"])==filter_status]
        if not shown:
            st.markdown('<p style="color:#6b7280;font-family:DM Mono,monospace;font-size:13px;padding:20px 0;">Nothing here yet.</p>', unsafe_allow_html=True)
            return

        for c in shown:
            conf  = c["confidence"]
            cc    = "#10b981" if conf>=85 else "#f59e0b" if conf>=65 else "#ef4444"
            dec_badge = "🔴 DUPLICATE" if c["decision"]=="DUPLICATE" else "🟡 NEEDS REVIEW"

            st.markdown(f"""
            <div style="background:#111827;border:1px solid #1f2937;border-radius:14px;padding:24px;margin-bottom:8px;">
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px;">
                    <span style="font-family:DM Mono,monospace;font-size:11px;color:#4b5563;">#{c['pair_id']}</span>
                    <span style="background:{cc}22;border:1px solid {cc}55;border-radius:20px;
                          padding:3px 12px;font-family:DM Mono,monospace;font-size:12px;color:{cc};">
                        {conf}% confidence
                    </span>
                    <span style="background:#1f2937;border-radius:20px;padding:3px 12px;
                          font-family:DM Mono,monospace;font-size:11px;color:#9ca3af;">
                        {dec_badge}
                    </span>
                    <span style="background:#1f293799;border-radius:20px;padding:3px 12px;
                          font-family:DM Mono,monospace;font-size:11px;color:#6b7280;">
                        matched: {c['matched_on']}
                    </span>
                </div>
                <p style="color:#6b7280;font-family:DM Mono,monospace;font-size:12px;
                   font-style:italic;margin:0;">"{c['reason']}"</p>
            </div>
            """, unsafe_allow_html=True)

            # Side by side
            col_a, col_mid, col_b = st.columns([5,1,5])
            ra, rb = c["rec_a"], c["rec_b"]
            all_keys = list(ra.keys())

            def field_card(title, color, rec, other_rec):
                st.markdown(f'<div style="background:#0d1117;border:1px solid #1f2937;border-radius:10px;padding:16px;">', unsafe_allow_html=True)
                st.markdown(f'<p style="color:{color};font-family:DM Mono,monospace;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:14px;">{title}</p>', unsafe_allow_html=True)
                for k in all_keys:
                    va = str(rec.get(k,"") or "")
                    vb = str(other_rec.get(k,"") or "")
                    match = va.lower().strip()==vb.lower().strip() and va.strip()!=""
                    vc = "#10b981" if match else "#f9fafb"
                    st.markdown(f"""
                    <div style="margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #1f2937;">
                        <div style="font-family:DM Mono,monospace;font-size:10px;color:#4b5563;text-transform:uppercase;margin-bottom:2px;">{k}</div>
                        <div style="font-size:13px;color:{vc};">{va or "—"}</div>
                    </div>""", unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)

            with col_a:
                field_card(f"Record A · {c['id_a']}", "#3b82f6", ra, rb)
            with col_mid:
                st.markdown('<div style="text-align:center;padding-top:80px;font-size:22px;color:#374151;">⟷</div>', unsafe_allow_html=True)
            with col_b:
                field_card(f"Record B · {c['id_b']}", "#6366f1", rb, ra)

            # Matching fields note
            st.markdown(f'<p style="font-family:DM Mono,monospace;font-size:11px;color:#374151;margin:8px 0 4px;">🟢 Green fields = matching values</p>', unsafe_allow_html=True)

            # Action buttons
            if filter_status == "PENDING":
                b1,b2,b3 = st.columns([2,2,3])
                with b1:
                    if st.button("✅ Approve", key=f"ap_{c['pair_id']}"):
                        st.session_state.decisions[c["pair_id"]]="APPROVED"; st.rerun()
                with b2:
                    if st.button("❌ Reject",  key=f"rj_{c['pair_id']}"):
                        st.session_state.decisions[c["pair_id"]]="REJECTED"; st.rerun()
            elif filter_status == "APPROVED":
                if st.button("↩ Undo Approve", key=f"ua_{c['pair_id']}"):
                    st.session_state.decisions[c["pair_id"]]="PENDING"; st.rerun()
            elif filter_status == "REJECTED":
                if st.button("↩ Undo Reject", key=f"ur_{c['pair_id']}"):
                    st.session_state.decisions[c["pair_id"]]="PENDING"; st.rerun()

            st.markdown("<br>", unsafe_allow_html=True)

    with tab1: render_pairs("PENDING")
    with tab2: render_pairs("APPROVED")
    with tab3: render_pairs("REJECTED")

    # Golden record button
    if approved > 0 and pending == 0:
        st.markdown("---")
        st.markdown(card("🏆 All pairs reviewed — Ready to build Golden Records"), unsafe_allow_html=True)
        st.markdown(f'<p style="color:#9ca3af;font-size:13px;margin-bottom:16px;">{approved} approved pairs will be merged into golden records. Two tables will be created in your schema: <b style="color:#f9fafb">{st.session_state.sel_table}_GOLDEN</b> and <b style="color:#f9fafb">{st.session_state.sel_table}_MDM_AUDIT</b>.</p>', unsafe_allow_html=True)
        if st.button("🏆 Build Golden Records & Write to Snowflake →"):
            st.session_state.page="golden"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    elif approved > 0:
        st.markdown("---")
        c1,c2 = st.columns(2)
        with c1:
            st.info(f"⏳ {pending} pairs still pending. Review all before building golden records, or:")
        with c2:
            st.markdown("<br>",unsafe_allow_html=True)
            if st.button("🏆 Build Golden Records now (with approved so far) →"):
                st.session_state.page="golden"; st.rerun()

# ══════════════════════════════════════════════════════════════════════
# PAGE 6 — GOLDEN RECORD
# ══════════════════════════════════════════════════════════════════════
def page_golden():
    topbar("Golden Record Builder", subtitle="Writing master records to Snowflake")
    st.markdown("---")

    db,sc,tb  = st.session_state.sel_db,st.session_state.sel_schema,st.session_state.sel_table
    cands     = st.session_state.candidates
    decisions = st.session_state.decisions
    approved  = [c for c in cands if decisions.get(c["pair_id"])=="APPROVED"]
    schema_info = st.session_state.table_schema or {}
    id_col    = schema_info.get("id_column", "ID")

    st.markdown('<div style="background:#111827;border:1px solid #1f2937;border-radius:14px;padding:32px;">', unsafe_allow_html=True)
    status = st.empty()
    bar    = st.progress(0)
    detail = st.empty()

    # Cluster approved pairs using union-find
    status.markdown(step_msg("Step 1/3","Clustering approved pairs into unique customer groups..."))
    bar.progress(15)

    parent = {}
    def find(x):
        parent.setdefault(x,x)
        if parent[x]!=x: parent[x]=find(parent[x])
        return parent[x]
    def union(x,y):
        parent[find(x)]=find(y)

    all_recs_map = {}
    for c in approved:
        union(c["id_a"],c["id_b"])
        all_recs_map[c["id_a"]] = c["rec_a"]
        all_recs_map[c["id_b"]] = c["rec_b"]

    clusters = defaultdict(list)
    for cid in all_recs_map:
        clusters[find(cid)].append(cid)

    detail.markdown(f'<p style="color:#6b7280;font-family:DM Mono,monospace;font-size:12px;">✓ {len(approved)} approved pairs → {len(clusters)} unique customer clusters</p>', unsafe_allow_html=True)

    # Survivorship — pick most complete value per field
    def best_value(values):
        clean = [v for v in values if v and str(v).strip() not in ("","None","nan","NULL")]
        if not clean: return ""
        return max(clean, key=lambda x: len(str(x)))

    status.markdown(step_msg("Step 2/3","Applying survivorship rules — picking best value per field..."))
    bar.progress(45)

    cols = list(next(iter(all_recs_map.values())).keys()) if all_recs_map else []
    golden_rows = []
    for gid,(root,cids) in enumerate(clusters.items()):
        recs   = [all_recs_map[c] for c in cids if c in all_recs_map]
        golden = {}
        for col in cols:
            golden[col] = best_value([r.get(col,"") for r in recs])
        golden["GOLDEN_ID"]    = f"GLD{str(gid+1).zfill(5)}"
        golden["SOURCE_IDS"]   = ", ".join(sorted(cids))
        golden["MERGED_COUNT"] = len(cids)
        golden["CREATED_AT"]   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        golden_rows.append(golden)

    status.markdown(step_msg("Step 3/3","Writing golden records to Snowflake..."))
    bar.progress(65)

    golden_tb = f"{tb}_GOLDEN"
    audit_tb  = f"{tb}_MDM_AUDIT"
    golden_df = pd.DataFrame(golden_rows)

    try:
        # Create golden table dynamically — columns from actual data
        col_defs = []
        for col in golden_df.columns:
            if col in ("MERGED_COUNT",):
                col_defs.append(f'"{col}" NUMBER')
            else:
                col_defs.append(f'"{col}" VARCHAR')
        sf_write(f'CREATE OR REPLACE TABLE "{db}"."{sc}"."{golden_tb}" ({", ".join(col_defs)})')

        for _,row in golden_df.iterrows():
            vals = []
            for v in row.values:
                if v is None or str(v).strip()=="":
                    vals.append("NULL")
                else:
                    vals.append(f"'{str(v).replace(chr(39),chr(39)*2)}'")
            sf_write(f'INSERT INTO "{db}"."{sc}"."{golden_tb}" VALUES ({", ".join(vals)})')

        detail.markdown(f'<p style="color:#10b981;font-family:DM Mono,monospace;font-size:12px;">✓ Created {db}.{sc}.{golden_tb} with {len(golden_rows)} golden records</p>', unsafe_allow_html=True)

        # Create audit table
        sf_write(f"""CREATE OR REPLACE TABLE "{db}"."{sc}"."{audit_tb}" (
            PAIR_ID VARCHAR, ID_A VARCHAR, ID_B VARCHAR,
            CONFIDENCE NUMBER, MATCHED_ON VARCHAR,
            REASON VARCHAR, AGENT_DECISION VARCHAR,
            HUMAN_DECISION VARCHAR, REVIEWED_AT VARCHAR
        )""")

        for c in cands:
            d = decisions.get(c["pair_id"],"PENDING")
            sf_write(f"""INSERT INTO "{db}"."{sc}"."{audit_tb}" VALUES (
                '{c["pair_id"]}','{c["id_a"]}','{c["id_b"]}',
                {c["confidence"]},
                '{c["matched_on"].replace("'","''")}',
                '{c["reason"].replace("'","''")}',
                '{c["decision"]}','{d}',
                '{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
            )""")

        bar.progress(100)
        status.markdown(step_msg("✅ Done","Golden records and audit trail written to Snowflake!"))
        st.markdown("</div>", unsafe_allow_html=True)

        st.success(f"✅ **{db}.{sc}.{golden_tb}** — {len(golden_rows)} golden master records")
        st.success(f"✅ **{db}.{sc}.{audit_tb}** — full audit trail for all {len(cands)} pairs")

        st.markdown("### Preview — Golden Records")
        st.dataframe(golden_df, use_container_width=True, hide_index=True)

        st.markdown("### Run these in Snowflake to verify")
        st.code(f"""-- Golden records
SELECT * FROM "{db}"."{sc}"."{golden_tb}";

-- Audit trail
SELECT * FROM "{db}"."{sc}"."{audit_tb}" ORDER BY CONFIDENCE DESC;

-- Summary
SELECT HUMAN_DECISION, COUNT(*) as CNT
FROM "{db}"."{sc}"."{audit_tb}"
GROUP BY HUMAN_DECISION;""", language="sql")

        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("← Back to Review"):
            st.session_state.page="hitl"; st.rerun()

    except Exception as e:
        bar.progress(0)
        st.markdown("</div>", unsafe_allow_html=True)
        st.error(f"Error writing to Snowflake: {e}")
        if st.button("← Back"):
            st.session_state.page="hitl"; st.rerun()

# ── Router ─────────────────────────────────────────────────────────────────────
pg = st.session_state.page
if not st.session_state.sf_conn and pg != "login":
    st.session_state.page = "login"; pg = "login"

if   pg=="login":  page_login()
elif pg=="setup":  page_setup()
elif pg=="table":  page_table()
elif pg=="agent":  page_agent()
elif pg=="hitl":   page_hitl()
elif pg=="golden": page_golden()
