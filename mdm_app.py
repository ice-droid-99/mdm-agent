import streamlit as st
import snowflake.connector
import pandas as pd
import google.generativeai as genai
import json, re, uuid
from datetime import datetime
from itertools import combinations
from collections import defaultdict

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="MDM Agent", page_icon="❄️", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@600;700;800&display=swap');
html,body,[data-testid="stAppViewContainer"]{background:#07090f !important;}
[data-testid="stAppViewContainer"]>.main{background:#07090f !important;}
[data-testid="stHeader"]{background:transparent !important;}
section[data-testid="stSidebar"]{display:none !important;}
#MainMenu,footer{visibility:hidden;}
*{font-family:'Syne',sans-serif;}
code,pre,[data-testid="stTextInput"] input,[data-testid="stSelectbox"] *{font-family:'DM Mono',monospace !important;}
[data-testid="stTextInput"] input{background:#0f1420 !important;border:1px solid #1e2535 !important;border-radius:8px !important;color:#e8eaf0 !important;font-size:13px !important;}
[data-testid="stTextInput"] input:focus{border-color:#4f8ef7 !important;box-shadow:0 0 0 3px rgba(79,142,247,0.12) !important;}
[data-testid="stTextInput"] label,[data-testid="stSelectbox"] label{color:#4b5680 !important;font-family:'DM Mono',monospace !important;font-size:11px !important;text-transform:uppercase !important;letter-spacing:1.5px !important;}
[data-testid="stSelectbox"]>div>div{background:#0f1420 !important;border:1px solid #1e2535 !important;border-radius:8px !important;color:#e8eaf0 !important;}
[data-testid="stButton"]>button{background:linear-gradient(135deg,#4f8ef7,#7c5ce8) !important;color:#fff !important;border:none !important;border-radius:8px !important;font-weight:700 !important;font-size:13px !important;width:100% !important;padding:12px !important;letter-spacing:0.5px !important;}
[data-testid="stButton"]>button:hover{opacity:.88 !important;transform:translateY(-1px) !important;}
[data-testid="stMetric"]{background:#0f1420;border:1px solid #1e2535;border-radius:12px;padding:20px !important;}
[data-testid="stMetricValue"]{color:#4f8ef7 !important;font-size:26px !important;font-weight:800 !important;}
[data-testid="stMetricLabel"]{color:#4b5680 !important;font-size:11px !important;}
[data-testid="stDataFrame"]{border:1px solid #1e2535 !important;border-radius:10px !important;}
hr{border-color:#1e2535 !important;}
.stTabs [data-baseweb="tab-list"]{background:#0f1420;border-radius:10px;gap:4px;padding:4px;}
.stTabs [data-baseweb="tab"]{border-radius:8px !important;color:#4b5680 !important;}
.stTabs [aria-selected="true"]{background:#1e2535 !important;color:#e8eaf0 !important;}
[data-testid="stProgress"]>div>div{background:#4f8ef7 !important;}
</style>
""", unsafe_allow_html=True)

# ── Session defaults ───────────────────────────────────────────────────────────
DEFAULTS = {
    "page":"login","sf_conn":None,"sf_account":"","sf_user":"",
    "warehouses":[],"databases":[],"schemas":[],"tables":[],
    "sel_db":None,"sel_schema":None,"sel_table":None,"sel_wh":None,
    "df":None,"total_rows":0,"current_page":1,"rows_per_page":50,
    "col_types":None,       # Gemini's column type map
    "id_col":None,          # which column is the primary key
    "clusters":[],          # list of clusters found
    "analysis_done":False,
    "cluster_decisions":{}, # cluster_id -> "APPROVED"/"REJECTED"/"PENDING"
    "gemini_key":"",
}
for k,v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ══════════════════════════════════════════════════════════════════════
# SNOWFLAKE
# ══════════════════════════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════════════════════════
# GEMINI
# ══════════════════════════════════════════════════════════════════════
def call_gemini(prompt, max_tokens=4000):
    genai.configure(api_key=st.session_state.gemini_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config=genai.GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=0.1,
        )
    )
    resp = model.generate_content(prompt)
    return resp.text.strip()

def call_gemini_json(prompt, max_tokens=4000):
    text = call_gemini(prompt, max_tokens)
    text = re.sub(r"```json|```", "", text).strip()
    try:
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if m: return json.loads(m.group(1))
    except: pass
    try:
        fixed  = text
        fixed += "]" * max(0, text.count("[") - text.count("]"))
        fixed += "}" * max(0, text.count("{") - text.count("}"))
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", fixed)
        if m: return json.loads(m.group(1))
    except: pass
    return {}

# ══════════════════════════════════════════════════════════════════════
# STEP 1 — Gemini understands table structure
# ══════════════════════════════════════════════════════════════════════
def understand_table(df):
    cols   = list(df.columns)
    sample = df.head(8).to_dict(orient="records")
    prompt = f"""You are an MDM expert. Analyze this customer table.

Columns: {cols}
Sample rows: {json.dumps(sample, default=str, indent=2)}

Return ONLY this JSON:
{{
  "id_column": "<primary key column name>",
  "column_types": {{
    "<col>": "<type>"
  }}
}}

Types: "id", "firstname", "lastname", "fullname", "email", "phone", "dob", "gender", "address", "city", "ip", "numeric_id", "other"
Be precise — split first/last name if separate columns."""
    result = call_gemini_json(prompt, max_tokens=1000)
    return result

# ══════════════════════════════════════════════════════════════════════
# STEP 2 — Python signal scoring (no AI, pure logic)
# ══════════════════════════════════════════════════════════════════════
def norm_phone(v):
    return re.sub(r"\D","",str(v or ""))[-10:]

def norm_dob(v):
    v = str(v or "").strip()
    if not v or v in ("None","nan",""): return ""
    try:
        from dateutil import parser as dp
        if v.isdigit() and len(v)==8:
            for fmt in ["%Y%m%d","%d%m%Y"]:
                try: return datetime.strptime(v,fmt).strftime("%Y-%m-%d")
                except: pass
        return dp.parse(v, dayfirst=True).strftime("%Y-%m-%d")
    except: return v.lower().strip()

def norm_name(v):
    return re.sub(r"[^a-z]","",str(v or "").lower().strip())

def norm_email(v):
    return str(v or "").lower().strip()

def norm_addr(v):
    return re.sub(r"[^a-z0-9]","",str(v or "").lower())

def signal_score(rec_a, rec_b, col_types):
    """
    Pure Python signal scoring. Returns (score, reasons_list).
    Scoring tiers:
      Tier 1 (definitive) : email=40, phone=35, numeric_id=30
      Tier 2 (strong combo): firstname+lastname=25, lastname+dob=20, firstname+dob=18
      Tier 3 (supporting) : dob=12, ip=8, city=4, gender=2
    """
    score   = 0
    reasons = []

    types   = col_types or {}

    # Collect values by type
    def get_vals(typ):
        a_vals, b_vals = [], []
        for col, t in types.items():
            if t == typ:
                a_vals.append(rec_a.get(col,""))
                b_vals.append(rec_b.get(col,""))
        return a_vals, b_vals

    # Email
    ae, be = get_vals("email")
    for av,bv in zip(ae,be):
        if norm_email(av) == norm_email(bv) and norm_email(av):
            score += 40; reasons.append("✅ Same email (+40)")

    # Phone
    ap, bp = get_vals("phone")
    for av,bv in zip(ap,bp):
        na,nb = norm_phone(av), norm_phone(bv)
        if na == nb and len(na) >= 8:
            score += 35; reasons.append("✅ Same phone (+35)")

    # Numeric ID (CIF etc)
    an, bn = get_vals("numeric_id")
    for av,bv in zip(an,bn):
        if str(av).strip() == str(bv).strip() and str(av).strip():
            score += 30; reasons.append("✅ Same CIF/ID (+30)")

    # First + Last name combo
    afn, bfn = get_vals("firstname")
    aln, bln = get_vals("lastname")
    fn_match  = any(norm_name(a)==norm_name(b) and norm_name(a)
                    for a,b in zip(afn,bfn))
    ln_match  = any(norm_name(a)==norm_name(b) and norm_name(a)
                    for a,b in zip(aln,bln))

    # DOB
    ad, bd = get_vals("dob")
    dob_match = any(norm_dob(a)==norm_dob(b) and norm_dob(a)
                    for a,b in zip(ad,bd))

    if fn_match and ln_match:
        score += 25; reasons.append("✅ Same full name (+25)")
    elif ln_match and dob_match:
        score += 20; reasons.append("✅ Same last name + DOB (+20)")
    elif fn_match and dob_match:
        score += 18; reasons.append("✅ Same first name + DOB (+18)")
    elif ln_match:
        pass  # last name alone → 0, not a signal
    elif fn_match:
        pass  # first name alone → 0

    # DOB alone (only if no name match already gave points)
    if dob_match and not (fn_match or ln_match):
        score += 12; reasons.append("✅ Same DOB (+12)")

    # IP
    ai, bi = get_vals("ip")
    for av,bv in zip(ai,bi):
        if str(av).strip() == str(bv).strip() and str(av).strip():
            score += 8; reasons.append("✅ Same IP (+8)")

    # City
    ac, bc = get_vals("city")
    for av,bv in zip(ac,bc):
        if norm_addr(av) == norm_addr(bv) and norm_addr(av):
            score += 4; reasons.append("✅ Same city (+4)")

    # Gender
    ag, bg = get_vals("gender")
    for av,bv in zip(ag,bg):
        if str(av).strip().upper() == str(bv).strip().upper() and str(av).strip():
            score += 2  # silent, too weak to show

    return score, reasons

# ══════════════════════════════════════════════════════════════════════
# STEP 3 — Gemini reviews only ambiguous pairs (30-69 score)
# ══════════════════════════════════════════════════════════════════════
def gemini_review_batch(pairs_data, col_types):
    """Send a batch of ambiguous pairs to Gemini. Returns {pair_index: result}."""
    if not pairs_data: return {}

    prompt = f"""You are an MDM expert. Review these customer record pairs.
Column types: {json.dumps(col_types)}

Pairs:
{json.dumps(pairs_data, default=str, indent=2)}

For each pair decide if they are the SAME real person.
Rules:
- "M. Ali", "Mohd Ali", "Mohammed Ali" = same name
- Dates in any format — normalize and compare the actual date
- Phone: strip country code, compare last 10 digits
- Address: ignore case and punctuation
- Same last name alone = NOT enough
- Same gender alone = NOT enough
- Need at least 2 independent signals to call DUPLICATE

Return ONLY a JSON array:
[
  {{
    "pair_index": <number>,
    "decision": "DUPLICATE" or "NOT_DUPLICATE",
    "confidence": <0-100>,
    "reason": "<one sentence>"
  }}
]"""

    try:
        results = call_gemini_json(prompt, max_tokens=3000)
        if isinstance(results, list):
            return {r["pair_index"]: r for r in results if "pair_index" in r}
    except: pass
    return {}

# ══════════════════════════════════════════════════════════════════════
# STEP 4 — Union-Find clustering
# ══════════════════════════════════════════════════════════════════════
class UnionFind:
    def __init__(self):
        self.parent = {}
    def find(self, x):
        self.parent.setdefault(x, x)
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]
    def union(self, x, y):
        self.parent[self.find(x)] = self.find(y)
    def clusters(self, nodes):
        groups = defaultdict(list)
        for n in nodes:
            groups[self.find(n)].append(n)
        return [g for g in groups.values() if len(g) > 1]

# ══════════════════════════════════════════════════════════════════════
# FULL ANALYSIS PIPELINE
# ══════════════════════════════════════════════════════════════════════
def run_analysis(all_df, status, bar, detail):
    cols    = list(all_df.columns)
    all_recs = all_df.to_dict(orient="records")
    n        = len(all_recs)

    # Step 1 — understand table
    status.markdown(smsg("1/4","Gemini reading table structure..."))
    bar.progress(5)
    schema = understand_table(all_df)
    col_types = schema.get("column_types", {})
    id_col    = schema.get("id_column", cols[0])
    st.session_state.col_types = col_types
    st.session_state.id_col    = id_col
    detail.markdown(info(f"ID column: {id_col} · Types: {', '.join([f'{k}={v}' for k,v in col_types.items()])}"))

    # Step 2 — Python signal scoring of all pairs
    status.markdown(smsg("2/4",f"Scoring all {n*(n-1)//2:,} pairs with signal engine..."))
    bar.progress(20)

    uf          = UnionFind()
    auto_links  = []   # score >= 70
    gemini_q    = []   # score 30-69
    dropped     = 0

    pair_scores = {}   # (i,j) -> (score, reasons)

    for i in range(n):
        for j in range(i+1, n):
            score, reasons = signal_score(all_recs[i], all_recs[j], col_types)
            if score >= 70:
                auto_links.append((i, j, score, reasons))
                pair_scores[(i,j)] = (score, reasons)
            elif score >= 30:
                gemini_q.append((i, j, score, reasons))
                pair_scores[(i,j)] = (score, reasons)
            else:
                dropped += 1

    detail.markdown(info(f"Auto-linked: {len(auto_links)} · Gemini review: {len(gemini_q)} · Dropped (noise): {dropped}"))

    # Auto-link high confidence pairs
    for i,j,score,reasons in auto_links:
        id_i = str(all_recs[i].get(id_col, f"row_{i}"))
        id_j = str(all_recs[j].get(id_col, f"row_{j}"))
        uf.union(id_i, id_j)

    # Step 3 — Gemini reviews ambiguous pairs in chunks of 10
    status.markdown(smsg("3/4",f"Gemini reviewing {len(gemini_q)} ambiguous pairs..."))
    bar.progress(40)

    CHUNK = 10
    for chunk_start in range(0, len(gemini_q), CHUNK):
        chunk = gemini_q[chunk_start:chunk_start+CHUNK]
        pairs_data = []
        for idx, (i, j, score, reasons) in enumerate(chunk):
            pairs_data.append({
                "pair_index": chunk_start + idx,
                "pre_score":  score,
                "signals":    reasons,
                "rec_a": all_recs[i],
                "rec_b": all_recs[j],
            })

        results = gemini_review_batch(pairs_data, col_types)

        for idx, (i, j, score, reasons) in enumerate(chunk):
            res = results.get(chunk_start + idx, {})
            if res.get("decision") == "DUPLICATE":
                id_i = str(all_recs[i].get(id_col, f"row_{i}"))
                id_j = str(all_recs[j].get(id_col, f"row_{j}"))
                uf.union(id_i, id_j)
                pair_scores[(i,j)] = (
                    max(score, int(res.get("confidence", score))),
                    reasons + [f"🤖 Gemini: {res.get('reason','')}"]
                )

        pct = 40 + int(((chunk_start+CHUNK)/max(len(gemini_q),1))*40)
        bar.progress(min(pct, 80))

    # Step 4 — Build clusters
    status.markdown(smsg("4/4","Building clusters..."))
    bar.progress(85)

    all_ids = [str(r.get(id_col, f"row_{i}")) for i,r in enumerate(all_recs)]
    id_to_rec = {str(r.get(id_col, f"row_{i}")): r for i,r in enumerate(all_recs)}
    raw_clusters = uf.clusters(all_ids)

    clusters = []
    for cids in raw_clusters:
        recs = [id_to_rec[c] for c in cids if c in id_to_rec]

        # Collect all signal scores for records in this cluster
        cluster_scores  = []
        cluster_reasons = []
        for ii in range(len(recs)):
            for jj in range(ii+1, len(recs)):
                ri = all_recs.index(recs[ii]) if recs[ii] in all_recs else -1
                rj = all_recs.index(recs[jj]) if recs[jj] in all_recs else -1
                if ri >= 0 and rj >= 0:
                    key = (min(ri,rj), max(ri,rj))
                    if key in pair_scores:
                        s, r = pair_scores[key]
                        cluster_scores.append(s)
                        cluster_reasons.extend(r)

        avg_score = int(sum(cluster_scores)/len(cluster_scores)) if cluster_scores else 50
        # deduplicate reasons
        seen_reasons = list(dict.fromkeys(cluster_reasons))

        clusters.append({
            "cluster_id":  str(uuid.uuid4())[:8].upper(),
            "record_ids":  cids,
            "records":     recs,
            "avg_score":   avg_score,
            "reasons":     seen_reasons,
            "record_count": len(cids),
        })

    # Sort by avg_score descending
    clusters.sort(key=lambda x: x["avg_score"], reverse=True)

    bar.progress(100)
    status.markdown(smsg("✅ Done", f"Found {len(clusters)} duplicate clusters across {sum(c['record_count'] for c in clusters)} records"))
    return clusters

# ══════════════════════════════════════════════════════════════════════
# UI HELPERS
# ══════════════════════════════════════════════════════════════════════
def smsg(label, msg):
    return f'<p style="font-family:DM Mono,monospace;font-size:13px;color:#8892b0;"><span style="color:#4f8ef7;font-weight:700;">{label}</span> · {msg}</p>'

def info(msg):
    return f'<p style="font-family:DM Mono,monospace;font-size:11px;color:#4b5680;margin:4px 0;">{msg}</p>'

def card(label):
    return f'<div style="background:#0f1420;border:1px solid #1e2535;border-radius:12px;padding:24px 28px;margin-bottom:16px;"><p style="color:#4f8ef7;font-family:DM Mono,monospace;font-size:11px;text-transform:uppercase;letter-spacing:2px;margin-bottom:16px;">{label}</p>'

def topbar(title, subtitle=""):
    h1,h2,h3 = st.columns([4,2,1])
    with h1:
        st.markdown(f'<h2 style="color:#e8eaf0;margin:0;font-size:22px;">❄️ {title}</h2>', unsafe_allow_html=True)
        if subtitle:
            st.markdown(f'<p style="color:#4b5680;font-family:DM Mono,monospace;font-size:11px;margin:4px 0 0;">{subtitle}</p>', unsafe_allow_html=True)
    with h2:
        steps  = ["login","setup","table","agent","hitl","golden"]
        labels = ["Login","Setup","View","Agent","Review","Golden"]
        cur    = st.session_state.page
        idx    = steps.index(cur) if cur in steps else 0
        html   = '<div style="display:flex;gap:4px;align-items:center;padding-top:10px;">'
        for i,lbl in enumerate(labels):
            bg = "#4f8ef7" if i==idx else ("#22c55e" if i<idx else "#1e2535")
            fc = "#fff" if i<=idx else "#4b5680"
            html += f'<div style="background:{bg};border-radius:4px;padding:3px 8px;font-family:DM Mono,monospace;font-size:9px;color:{fc};">{lbl}</div>'
        html += '</div>'
        st.markdown(html, unsafe_allow_html=True)
    with h3:
        if st.button("Logout", key="logout_btn"):
            for k in list(st.session_state.keys()): del st.session_state[k]
            st.rerun()

def score_bar(score, reasons):
    """Render a visual score bar with breakdown."""
    color = "#22c55e" if score>=70 else "#f59e0b" if score>=40 else "#ef4444"
    label = "High confidence" if score>=70 else "Medium confidence" if score>=40 else "Low confidence"
    reasons_html = "".join([
        f'<span style="background:#1e2535;border-radius:4px;padding:2px 8px;font-family:DM Mono,monospace;font-size:10px;color:#8892b0;margin:2px;">{r}</span>'
        for r in reasons[:8]
    ])
    return f"""
    <div style="background:#0a0d14;border:1px solid #1e2535;border-radius:10px;padding:14px 18px;margin:8px 0;">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">
            <div style="font-family:DM Mono,monospace;font-size:11px;color:#4b5680;text-transform:uppercase;letter-spacing:1px;">Match Score</div>
            <div style="font-size:22px;font-weight:800;color:{color};">{score}</div>
            <div style="flex:1;background:#1e2535;border-radius:4px;height:6px;">
                <div style="background:{color};width:{min(score,100)}%;height:6px;border-radius:4px;"></div>
            </div>
            <div style="font-family:DM Mono,monospace;font-size:11px;color:{color};">{label}</div>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:4px;">{reasons_html}</div>
    </div>"""

# ══════════════════════════════════════════════════════════════════════
# PAGE 1 — LOGIN
# ══════════════════════════════════════════════════════════════════════
def page_login():
    st.markdown("<br><br>", unsafe_allow_html=True)
    _,c,_ = st.columns([1,1.1,1])
    with c:
        st.markdown("""
        <div style="text-align:center;margin-bottom:32px;">
            <div style="font-size:56px;margin-bottom:10px;">❄️</div>
            <div style="font-size:28px;font-weight:800;color:#e8eaf0;">MDM Agent</div>
            <div style="font-family:'DM Mono',monospace;font-size:12px;color:#2d3555;
                 margin-top:6px;letter-spacing:3px;">MASTER DATA MANAGEMENT · AI POWERED</div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown('<div style="background:#0f1420;border:1px solid #1e2535;border-radius:14px;padding:32px 36px;">', unsafe_allow_html=True)
        account  = st.text_input("Snowflake Account",  placeholder="xy12345.us-east-1")
        username = st.text_input("Username",            placeholder="your_username")
        password = st.text_input("Password", type="password", placeholder="••••••••")
        api_key  = st.text_input("Gemini API Key",     type="password", placeholder="AIza...")
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Connect & Start →"):
            if not all([account,username,password,api_key]):
                st.error("Please fill all fields.")
            else:
                with st.spinner("Connecting..."):
                    try:
                        conn = snowflake.connector.connect(
                            account=account.strip(), user=username.strip(),
                            password=password.strip(), login_timeout=15)
                        st.session_state.sf_conn    = conn
                        st.session_state.sf_account = account.strip()
                        st.session_state.sf_user    = username.strip()
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
        st.markdown('<p style="text-align:center;color:#1e2535;font-family:DM Mono,monospace;font-size:11px;margin-top:14px;">🔒 Session only · Nothing stored</p>', unsafe_allow_html=True)

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
            try:
                cur = st.session_state.sf_conn.cursor()
                cur.execute(f'SHOW SCHEMAS IN DATABASE "{db}"')
                st.session_state.schemas = [r[1] for r in cur.fetchall()]
                cur.close()
            except Exception as e: st.error(str(e))
        schema = st.selectbox("Schema", st.session_state.schemas, key="sc_s") if st.session_state.schemas else None
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
        table = st.selectbox("Table", st.session_state.tables, key="tb_s") if st.session_state.tables else None
    st.markdown("</div>", unsafe_allow_html=True)

    if wh and db and schema and table:
        ca,cb = st.columns([1,2])
        with ca: rpp = st.selectbox("Rows/page",[25,50,100,200],index=1)
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
                        st.session_state.df            = get_page(rpp,0)
                        st.session_state.current_page  = 1
                        st.session_state.analysis_done = False
                        st.session_state.clusters      = []
                        st.session_state.cluster_decisions = {}
                        st.session_state.page = "table"
                        st.rerun()
                    except Exception as e: st.error(str(e))

# ══════════════════════════════════════════════════════════════════════
# PAGE 3 — TABLE VIEWER
# ══════════════════════════════════════════════════════════════════════
def page_table():
    db,sc,tb = st.session_state.sel_db,st.session_state.sel_schema,st.session_state.sel_table
    rpp   = st.session_state.rows_per_page
    total = st.session_state.total_rows
    pg    = st.session_state.current_page
    tp    = max(1,-(-total//rpp))
    s,e   = (pg-1)*rpp+1, min(pg*rpp,total)

    topbar(f"{tb}", subtitle=f"{db} · {sc}")
    st.markdown("---")

    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Total Rows",f"{total:,}")
    m2.metric("Columns",   len(st.session_state.df.columns))
    m3.metric("Page",      f"{pg}/{tp}")
    m4.metric("Rows/Page", rpp)
    st.markdown("<br>", unsafe_allow_html=True)

    st.dataframe(st.session_state.df, use_container_width=True, height=480, hide_index=True)

    p1,p2,p3,p4,p5 = st.columns([1,1,2,1,1])
    with p1:
        if st.button("⟨⟨ First",disabled=(pg==1),key="bf"):
            st.session_state.current_page=1; st.session_state.df=get_page(rpp,0); st.rerun()
    with p2:
        if st.button("⟨ Prev", disabled=(pg==1),key="bp"):
            st.session_state.current_page-=1
            st.session_state.df=get_page(rpp,(st.session_state.current_page-1)*rpp); st.rerun()
    with p3:
        st.markdown(f'<div style="text-align:center;padding:10px 0;font-family:DM Mono,monospace;font-size:12px;color:#4b5680;">Rows <b style="color:#4f8ef7">{s:,}–{e:,}</b> of <b style="color:#e8eaf0">{total:,}</b></div>', unsafe_allow_html=True)
    with p4:
        if st.button("Next ⟩", disabled=(pg>=tp),key="bn"):
            st.session_state.current_page+=1
            st.session_state.df=get_page(rpp,(st.session_state.current_page-1)*rpp); st.rerun()
    with p5:
        if st.button("Last ⟩⟩",disabled=(pg>=tp),key="bl"):
            st.session_state.current_page=tp
            st.session_state.df=get_page(rpp,(tp-1)*rpp); st.rerun()

    j1,j2,_ = st.columns([1,1,4])
    with j1: jump = st.number_input("Jump to page",1,tp,pg,1)
    with j2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Go →",key="bj"):
            st.session_state.current_page=int(jump)
            st.session_state.df=get_page(rpp,(int(jump)-1)*rpp); st.rerun()

    st.markdown("---")
    st.markdown(card("🤖 MDM Duplicate Detection Agent"), unsafe_allow_html=True)
    st.markdown('<p style="color:#8892b0;font-size:13px;margin-bottom:8px;">Detects duplicate clusters of <b style="color:#e8eaf0">any size</b> — 2, 3, 5 or more records can all be the same person. Uses signal scoring first, Gemini only for ambiguous cases.</p>', unsafe_allow_html=True)
    if st.button("🔍 Run MDM Agent →"):
        st.session_state.page="agent"; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════
# PAGE 4 — AGENT
# ══════════════════════════════════════════════════════════════════════
def page_agent():
    topbar("MDM Agent", subtitle="Signal scoring + cluster detection")
    st.markdown("---")
    db,sc,tb = st.session_state.sel_db,st.session_state.sel_schema,st.session_state.sel_table

    if not st.session_state.analysis_done:
        st.markdown('<div style="background:#0f1420;border:1px solid #1e2535;border-radius:14px;padding:32px;">', unsafe_allow_html=True)
        status = st.empty()
        bar    = st.progress(0)
        detail = st.empty()

        all_df  = sf_query(f'SELECT * FROM "{db}"."{sc}"."{tb}"')
        clusters = run_analysis(all_df, status, bar, detail)

        st.session_state.clusters          = clusters
        st.session_state.analysis_done     = True
        st.session_state.cluster_decisions = {c["cluster_id"]:"PENDING" for c in clusters}
        st.markdown("</div>", unsafe_allow_html=True)
        st.rerun()

    else:
        clusters = st.session_state.clusters
        total_recs = sum(c["record_count"] for c in clusters)

        st.success(f"✅ Found **{len(clusters)} duplicate clusters** covering **{total_recs} records**")

        m1,m2,m3 = st.columns(3)
        m1.metric("Clusters Found",    len(clusters))
        m2.metric("Records Affected",  total_recs)
        m3.metric("Records to Merge",  total_recs - len(clusters))

        if st.session_state.col_types:
            with st.expander("🧠 Table structure Gemini detected", expanded=False):
                st.json(st.session_state.col_types)

        st.markdown("<br>", unsafe_allow_html=True)
        c1,c2 = st.columns(2)
        with c1:
            if st.button("👤 Go to Human Review →"):
                st.session_state.page="hitl"; st.rerun()
        with c2:
            if st.button("🔄 Re-run Analysis"):
                st.session_state.analysis_done=False
                st.session_state.clusters=[]
                st.rerun()

# ══════════════════════════════════════════════════════════════════════
# PAGE 5 — HITL CLUSTER REVIEW
# ══════════════════════════════════════════════════════════════════════
def page_hitl():
    topbar("Human Review", subtitle="Review duplicate clusters — approve or reject")
    st.markdown("---")

    clusters  = st.session_state.clusters
    decisions = st.session_state.cluster_decisions

    if not clusters:
        st.info("No clusters found. Run the agent first.")
        if st.button("← Back"): st.session_state.page="table"; st.rerun()
        return

    approved = sum(1 for v in decisions.values() if v=="APPROVED")
    rejected = sum(1 for v in decisions.values() if v=="REJECTED")
    pending  = sum(1 for v in decisions.values() if v=="PENDING")

    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Total Clusters", len(clusters))
    m2.metric("✅ Approved",    approved)
    m3.metric("❌ Rejected",    rejected)
    m4.metric("⏳ Pending",     pending)

    # Bulk actions
    st.markdown("<br>", unsafe_allow_html=True)
    ba,bb,bc = st.columns(3)
    with ba: thresh = st.slider("Bulk approve threshold (score)", 30, 100, 70)
    with bb:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button(f"✅ Approve all score ≥ {thresh}"):
            for c in clusters:
                if c["avg_score"] >= thresh:
                    decisions[c["cluster_id"]] = "APPROVED"
            st.rerun()
    with bc:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("❌ Reject all score < 40"):
            for c in clusters:
                if c["avg_score"] < 40:
                    decisions[c["cluster_id"]] = "REJECTED"
            st.rerun()

    st.markdown("---")

    tab1,tab2,tab3 = st.tabs([
        f"⏳ Pending ({pending})",
        f"✅ Approved ({approved})",
        f"❌ Rejected ({rejected})"
    ])

    def render_clusters(filter_status):
        shown = [c for c in clusters if decisions.get(c["cluster_id"])==filter_status]
        if not shown:
            st.markdown('<p style="color:#4b5680;font-family:DM Mono,monospace;font-size:13px;padding:20px 0;">Nothing here yet.</p>', unsafe_allow_html=True)
            return

        for cluster in shown:
            cid    = cluster["cluster_id"]
            score  = cluster["avg_score"]
            recs   = cluster["records"]
            reasons= cluster["reasons"]
            n_recs = cluster["record_count"]

            sc_color = "#22c55e" if score>=70 else "#f59e0b" if score>=40 else "#ef4444"

            # Cluster header
            st.markdown(f"""
            <div style="background:#0f1420;border:1px solid #1e2535;border-radius:14px;
                 padding:20px 24px;margin-bottom:8px;">
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px;">
                    <span style="font-family:DM Mono,monospace;font-size:11px;color:#4b5680;">CLUSTER #{cid}</span>
                    <span style="background:{sc_color}22;border:1px solid {sc_color}55;border-radius:20px;
                          padding:3px 14px;font-family:DM Mono,monospace;font-size:12px;color:{sc_color};">
                        score {score}
                    </span>
                    <span style="background:#1e2535;border-radius:20px;padding:3px 14px;
                          font-family:DM Mono,monospace;font-size:12px;color:#8892b0;">
                        {n_recs} records
                    </span>
                </div>
            """, unsafe_allow_html=True)

            # Score bar with signal breakdown
            st.markdown(score_bar(score, reasons), unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

            # ALL records in cluster shown as a comparison table
            if recs:
                all_keys = list(recs[0].keys())
                id_col   = st.session_state.id_col or all_keys[0]

                # Build comparison — one column per record
                cols_layout = st.columns(n_recs)

                for ri, rec in enumerate(recs):
                    rec_id = str(rec.get(id_col,"?"))
                    col_color = ["#4f8ef7","#7c5ce8","#22c55e","#f59e0b","#ef4444",
                                 "#06b6d4","#ec4899","#84cc16","#f97316","#8b5cf6"]
                    cc = col_color[ri % len(col_color)]

                    with cols_layout[ri]:
                        st.markdown(f'<div style="background:#0a0d14;border:1px solid #1e2535;border-radius:10px;padding:14px;">', unsafe_allow_html=True)
                        st.markdown(f'<p style="color:{cc};font-family:DM Mono,monospace;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;">Record {ri+1} · {rec_id}</p>', unsafe_allow_html=True)

                        for k in all_keys:
                            val = str(rec.get(k,"") or "")
                            # check if all records match on this field
                            all_vals = [str(r.get(k,"") or "").lower().strip() for r in recs]
                            is_match = len(set(all_vals))==1 and all_vals[0]!=""
                            fc = "#22c55e" if is_match else "#e8eaf0"
                            st.markdown(f"""
                            <div style="margin-bottom:7px;padding-bottom:7px;border-bottom:1px solid #1e2535;">
                                <div style="font-family:DM Mono,monospace;font-size:9px;color:#2d3555;
                                     text-transform:uppercase;margin-bottom:2px;">{k}</div>
                                <div style="font-size:12px;color:{fc};">{val or "—"}</div>
                            </div>""", unsafe_allow_html=True)
                        st.markdown("</div>", unsafe_allow_html=True)

                st.markdown('<p style="font-family:DM Mono,monospace;font-size:10px;color:#2d3555;margin:6px 0 16px;">🟢 Green = all records match on this field</p>', unsafe_allow_html=True)

            # Action buttons
            if filter_status == "PENDING":
                b1,b2,_ = st.columns([2,2,3])
                with b1:
                    if st.button(f"✅ Approve cluster", key=f"ap_{cid}"):
                        st.session_state.cluster_decisions[cid]="APPROVED"; st.rerun()
                with b2:
                    if st.button(f"❌ Reject cluster",  key=f"rj_{cid}"):
                        st.session_state.cluster_decisions[cid]="REJECTED"; st.rerun()
            elif filter_status=="APPROVED":
                if st.button("↩ Undo", key=f"ua_{cid}"):
                    st.session_state.cluster_decisions[cid]="PENDING"; st.rerun()
            elif filter_status=="REJECTED":
                if st.button("↩ Undo", key=f"ur_{cid}"):
                    st.session_state.cluster_decisions[cid]="PENDING"; st.rerun()

            st.markdown("<br>", unsafe_allow_html=True)

    with tab1: render_clusters("PENDING")
    with tab2: render_clusters("APPROVED")
    with tab3: render_clusters("REJECTED")

    if approved > 0:
        st.markdown("---")
        st.markdown(card(f"🏆 Build Golden Records — {approved} cluster(s) approved"), unsafe_allow_html=True)
        st.markdown(f'<p style="color:#8892b0;font-size:13px;margin-bottom:16px;">Will create <b style="color:#e8eaf0">{st.session_state.sel_table}_GOLDEN</b> and <b style="color:#e8eaf0">{st.session_state.sel_table}_MDM_AUDIT</b> in your schema.</p>', unsafe_allow_html=True)
        if st.button("🏆 Build Golden Records →"):
            st.session_state.page="golden"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════
# PAGE 6 — GOLDEN RECORD
# ══════════════════════════════════════════════════════════════════════
def page_golden():
    topbar("Golden Record Builder", subtitle="Merging clusters into master records")
    st.markdown("---")

    db,sc,tb  = st.session_state.sel_db,st.session_state.sel_schema,st.session_state.sel_table
    clusters  = st.session_state.clusters
    decisions = st.session_state.cluster_decisions
    approved  = [c for c in clusters if decisions.get(c["cluster_id"])=="APPROVED"]
    id_col    = st.session_state.id_col or "ID"

    st.markdown('<div style="background:#0f1420;border:1px solid #1e2535;border-radius:14px;padding:32px;">', unsafe_allow_html=True)
    status = st.empty()
    bar    = st.progress(0)

    status.markdown(smsg("1/3","Applying survivorship rules — picking best value per field..."))
    bar.progress(20)

    def best_val(values):
        clean = [v for v in values if v and str(v).strip() not in ("","None","nan","NULL")]
        return max(clean, key=lambda x: len(str(x))) if clean else ""

    all_cols    = list(approved[0]["records"][0].keys()) if approved else []
    golden_rows = []

    for gid, cluster in enumerate(approved):
        recs   = cluster["records"]
        golden = {}
        for col in all_cols:
            golden[col] = best_val([r.get(col,"") for r in recs])
        golden["GOLDEN_ID"]    = f"GLD{str(gid+1).zfill(5)}"
        golden["SOURCE_IDS"]   = ", ".join(str(r.get(id_col,"")) for r in recs)
        golden["MERGED_COUNT"] = len(recs)
        golden["MATCH_SCORE"]  = cluster["avg_score"]
        golden["SIGNALS"]      = " | ".join(cluster["reasons"][:5])
        golden["CREATED_AT"]   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        golden_rows.append(golden)

    bar.progress(50)
    status.markdown(smsg("2/3","Writing golden records to Snowflake..."))

    golden_tb = f"{tb}_GOLDEN"
    audit_tb  = f"{tb}_MDM_AUDIT"
    golden_df = pd.DataFrame(golden_rows)

    try:
        col_defs = []
        for col in golden_df.columns:
            if col in ("MERGED_COUNT","MATCH_SCORE"):
                col_defs.append(f'"{col}" NUMBER')
            else:
                col_defs.append(f'"{col}" VARCHAR')
        sf_write(f'CREATE OR REPLACE TABLE "{db}"."{sc}"."{golden_tb}" ({", ".join(col_defs)})')

        for _,row in golden_df.iterrows():
            vals = []
            for v in row.values:
                if v is None or str(v).strip() in ("","nan"):
                    vals.append("NULL")
                else:
                    vals.append(f"'{str(v).replace(chr(39),chr(39)*2)}'")
            sf_write(f'INSERT INTO "{db}"."{sc}"."{golden_tb}" VALUES ({", ".join(vals)})')

        # Audit table
        sf_write(f"""CREATE OR REPLACE TABLE "{db}"."{sc}"."{audit_tb}" (
            CLUSTER_ID VARCHAR, RECORD_IDS VARCHAR, RECORD_COUNT NUMBER,
            MATCH_SCORE NUMBER, SIGNALS VARCHAR,
            HUMAN_DECISION VARCHAR, REVIEWED_AT VARCHAR)""")

        for cluster in clusters:
            d = decisions.get(cluster["cluster_id"],"PENDING")
            ids = ", ".join(cluster["record_ids"])
            sigs = " | ".join(cluster["reasons"][:5]).replace("'","''")
            sf_write(f"""INSERT INTO "{db}"."{sc}"."{audit_tb}" VALUES (
                '{cluster["cluster_id"]}','{ids}',{cluster["record_count"]},
                {cluster["avg_score"]},'{sigs}','{d}',
                '{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')""")

        bar.progress(100)
        status.markdown(smsg("✅ Done","Golden records and audit trail written!"))
        st.markdown("</div>", unsafe_allow_html=True)

        st.success(f"✅ **{db}.{sc}.{golden_tb}** — {len(golden_rows)} golden records")
        st.success(f"✅ **{db}.{sc}.{audit_tb}** — audit trail for all {len(clusters)} clusters")

        st.markdown("### Preview — Golden Records")
        st.dataframe(golden_df, use_container_width=True, hide_index=True)

        st.markdown("### Verify in Snowflake")
        st.code(f"""SELECT * FROM "{db}"."{sc}"."{golden_tb}";
SELECT * FROM "{db}"."{sc}"."{audit_tb}" ORDER BY MATCH_SCORE DESC;
SELECT HUMAN_DECISION, COUNT(*) FROM "{db}"."{sc}"."{audit_tb}" GROUP BY 1;""", language="sql")

        if st.button("← Back to Review"):
            st.session_state.page="hitl"; st.rerun()

    except Exception as e:
        st.markdown("</div>", unsafe_allow_html=True)
        st.error(f"Error: {e}")
        if st.button("← Back"):
            st.session_state.page="hitl"; st.rerun()

# ── Router ─────────────────────────────────────────────────────────────────────
pg = st.session_state.page
if not st.session_state.sf_conn and pg != "login":
    st.session_state.page="login"; pg="login"

if   pg=="login":  page_login()
elif pg=="setup":  page_setup()
elif pg=="table":  page_table()
elif pg=="agent":  page_agent()
elif pg=="hitl":   page_hitl()
elif pg=="golden": page_golden()
