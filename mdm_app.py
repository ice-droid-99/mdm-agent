import streamlit as st
import snowflake.connector
import pandas as pd
import google.generativeai as genai
import json, re, uuid
from datetime import datetime
from collections import defaultdict
from itertools import combinations

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
[data-testid="stButton"]>button{background:linear-gradient(135deg,#4f8ef7,#7c5ce8) !important;color:#fff !important;border:none !important;border-radius:8px !important;font-weight:700 !important;font-size:13px !important;width:100% !important;padding:12px !important;}
[data-testid="stButton"]>button:hover{opacity:.88 !important;transform:translateY(-1px) !important;}
[data-testid="stMetric"]{background:#0f1420;border:1px solid #1e2535;border-radius:12px;padding:20px !important;}
[data-testid="stMetricValue"]{color:#4f8ef7 !important;font-size:26px !important;font-weight:800 !important;}
[data-testid="stMetricLabel"]{color:#4b5680 !important;font-size:11px !important;}
[data-testid="stDataFrame"]{border:1px solid #1e2535 !important;border-radius:10px !important;}
hr{border-color:#1e2535 !important;}
.stTabs [data-baseweb="tab-list"]{background:#0f1420;border-radius:10px;gap:4px;padding:4px;}
.stTabs [data-baseweb="tab"]{border-radius:8px !important;color:#4b5680 !important;}
.stTabs [aria-selected="true"]{background:#1e2535 !important;color:#e8eaf0 !important;}
[data-testid="stProgress"]>div>div{background:linear-gradient(90deg,#4f8ef7,#7c5ce8) !important;}
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────────
for k, v in {
    "page":"login","sf_conn":None,"sf_account":"","sf_user":"",
    "warehouses":[],"databases":[],"schemas":[],"tables":[],
    "sel_db":None,"sel_schema":None,"sel_table":None,"sel_wh":None,
    "df":None,"total_rows":0,"current_page":1,"rows_per_page":50,
    "col_map":None,"id_col":None,
    "clusters":[],"analysis_done":False,"decisions":{},"gemini_key":"",
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ══════════════════════════════════════════════════════════════════════
# SNOWFLAKE HELPERS
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

def fetch_page(limit, offset):
    db,sc,tb = st.session_state.sel_db,st.session_state.sel_schema,st.session_state.sel_table
    return sf_query(f'SELECT * FROM "{db}"."{sc}"."{tb}" LIMIT {limit} OFFSET {offset}')

def fetch_count():
    db,sc,tb = st.session_state.sel_db,st.session_state.sel_schema,st.session_state.sel_table
    r = sf_query(f'SELECT COUNT(*) AS C FROM "{db}"."{sc}"."{tb}"')
    return int(r["C"].iloc[0])

# ══════════════════════════════════════════════════════════════════════
# GEMINI HELPERS
# ══════════════════════════════════════════════════════════════════════
def gemini_call(prompt, max_tokens=3000):
    genai.configure(api_key=st.session_state.gemini_key)
    model = genai.GenerativeModel(
        model_name="gemini-3.1-flash-lite",
        generation_config=genai.GenerationConfig(max_output_tokens=max_tokens, temperature=0.1)
    )
    return model.generate_content(prompt).text.strip()

def gemini_json(prompt, max_tokens=3000):
    raw = gemini_call(prompt, max_tokens)
    raw = re.sub(r"```json|```", "", raw).strip()
    for pattern in [r"(\[[\s\S]*\])", r"(\{[\s\S]*\})"]:
        try:
            m = re.search(pattern, raw)
            if m:
                return json.loads(m.group(1))
        except:
            pass
    return []

# ══════════════════════════════════════════════════════════════════════
# STEP 1 — GEMINI UNDERSTANDS TABLE STRUCTURE
# Returns col_map: {col_name: type}
# Types: id, firstname, lastname, email, phone, dob, address, city,
#        ip, numeric_id, gender, other
# ══════════════════════════════════════════════════════════════════════
def understand_table(df):
    sample = df.head(6).to_dict(orient="records")
    prompt = f"""You are an MDM expert. Analyze this customer table.

Columns: {list(df.columns)}
Sample data: {json.dumps(sample, default=str)}

Return ONLY a JSON object like this:
{{
  "id_column": "<the unique ID column name>",
  "col_map": {{
    "<column_name>": "<type>"
  }}
}}

Use ONLY these types:
- "id"         : unique row identifier
- "firstname"  : first / given name
- "lastname"   : last / family / surname
- "fullname"   : combined full name
- "email"      : email address
- "phone"      : phone or mobile number
- "dob"        : date of birth in any format
- "address"    : street address
- "city"       : city or town
- "ip"         : IP address
- "numeric_id" : CIF, account number, or any numeric identifier
- "gender"     : gender or sex field
- "other"      : anything else

Be precise. Infer from both the column name AND sample values."""

    result = gemini_json(prompt, max_tokens=800)
    if isinstance(result, dict) and result:
        id_col  = result.get("id_column", "") or list(df.columns)[0]
        col_map = result.get("col_map", {})
        if col_map:
            return id_col, col_map
    # Fallback: auto-detect from column names if Gemini fails
    col_map = {}
    id_col  = list(df.columns)[0]
    for col in df.columns:
        c = col.upper()
        if any(x in c for x in ["_ID","ID_","CID","CUSTOMER_ID","CUST_ID"]) and col == list(df.columns)[0]:
            col_map[col] = "id"
        elif "FIRST" in c and "NAME" in c:
            col_map[col] = "firstname"
        elif "LAST" in c and "NAME" in c:
            col_map[col] = "lastname"
        elif "FULL" in c and "NAME" in c:
            col_map[col] = "fullname"
        elif "EMAIL" in c or "MAIL" in c:
            col_map[col] = "email"
        elif any(x in c for x in ["PHONE","MOBILE","CONTACT","MOB"]):
            col_map[col] = "phone"
        elif any(x in c for x in ["DOB","BIRTH","DATE_OF"]):
            col_map[col] = "dob"
        elif "GENDER" in c or "SEX" in c:
            col_map[col] = "gender"
        elif any(x in c for x in ["ADDR","ADDRESS","STREET"]):
            col_map[col] = "address"
        elif "CITY" in c or "TOWN" in c:
            col_map[col] = "city"
        elif "IP" in c or "IP_ADDR" in c:
            col_map[col] = "ip"
        elif any(x in c for x in ["CIF","ACCOUNT","ACC_NO","ACCT"]):
            col_map[col] = "numeric_id"
        elif any(x in c for x in ["_ID","ID"]):
            col_map[col] = "id"
            if not id_col: id_col = col
        else:
            col_map[col] = "other"
    return id_col, col_map

# ══════════════════════════════════════════════════════════════════════
# NORMALISATION — purely Python, no AI
# ══════════════════════════════════════════════════════════════════════
def n_email(v):
    return str(v or "").strip().lower()

def n_phone(v):
    digits = re.sub(r"\D", "", str(v or ""))
    return digits[-10:] if len(digits) >= 10 else digits

def n_name(v):
    # remove punctuation, lowercase, strip spaces
    return re.sub(r"[^a-z]", "", str(v or "").lower())

def n_dob(v):
    v = str(v or "").strip()
    if not v or v in ("None","nan","NULL",""): return ""
    try:
        from dateutil import parser as dp
        if re.match(r"^\d{8}$", v):
            for fmt in ["%Y%m%d", "%d%m%Y"]:
                try: return datetime.strptime(v, fmt).strftime("%Y-%m-%d")
                except: pass
        return dp.parse(v, dayfirst=True).strftime("%Y-%m-%d")
    except:
        return v.lower()

def n_addr(v):
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())

def n_ip(v):
    return str(v or "").strip()

def get_col(rec, col_map, typ):
    """Get all values from record matching a given type."""
    return [rec.get(col, "") for col, t in col_map.items() if t == typ]

def first_val(lst):
    return lst[0] if lst else ""

# ══════════════════════════════════════════════════════════════════════
# STEP 2 — BLOCKING
# Only compare records that share at least one strong anchor:
#   - same email
#   - same phone (last 10 digits)
#   - same numeric_id (CIF)
#   - same lastname (normalized) — weak anchor, needs scoring to confirm
# ══════════════════════════════════════════════════════════════════════
def build_candidate_pairs(records, col_map):
    blocks = defaultdict(set)

    for i, rec in enumerate(records):
        # Email block
        for v in get_col(rec, col_map, "email"):
            nv = n_email(v)
            if nv and "@" in nv:
                blocks[f"email::{nv}"].add(i)

        # Phone block
        for v in get_col(rec, col_map, "phone"):
            nv = n_phone(v)
            if len(nv) >= 8:
                blocks[f"phone::{nv}"].add(i)

        # Numeric ID block (CIF etc) — normalize e.g. CIF9001 -> 9001
        for v in get_col(rec, col_map, "numeric_id"):
            nv = str(v or "").strip()
            if nv and nv not in ("","None","nan"):
                blocks[f"cif::{nv}"].add(i)
                # also index digits-only version for mixed formats
                digits_only = re.sub(r"[^0-9]", "", nv)
                if digits_only and digits_only != nv:
                    blocks[f"cif::{digits_only}"].add(i)

        # Last name block (weak anchor — needs score to confirm)
        for v in get_col(rec, col_map, "lastname"):
            nv = n_name(v)
            if len(nv) >= 3:
                blocks[f"ln::{nv}"].add(i)

        # Full name block
        for v in get_col(rec, col_map, "fullname"):
            nv = n_name(v)
            if len(nv) >= 4:
                blocks[f"fn::{nv}"].add(i)

    pairs = set()
    for grp in blocks.values():
        if len(grp) < 2: continue
        for a, b in combinations(sorted(grp), 2):
            pairs.add((min(a,b), max(a,b)))

    return list(pairs)

# ══════════════════════════════════════════════════════════════════════
# STEP 3 — SIGNAL SCORING
# After blocking brings records together, score the evidence
# ══════════════════════════════════════════════════════════════════════
def score_pair(rec_a, rec_b, col_map):
    score   = 0
    signals = []

    # ── Email (strong proof alone) ─────────────────────────────────
    for v in get_col(rec_a, col_map, "email"):
        for u in get_col(rec_b, col_map, "email"):
            if n_email(v) == n_email(u) and n_email(v):
                score += 40
                signals.append("✅ Same email (+40)")

    # ── Phone (strong proof alone) ─────────────────────────────────
    for v in get_col(rec_a, col_map, "phone"):
        for u in get_col(rec_b, col_map, "phone"):
            na, nb = n_phone(v), n_phone(u)
            if na == nb and len(na) >= 8:
                score += 35
                signals.append("✅ Same phone (+35)")

    # ── Numeric ID / CIF (definitive) ─────────────────────────────
    for v in get_col(rec_a, col_map, "numeric_id"):
        for u in get_col(rec_b, col_map, "numeric_id"):
            nv = str(v or "").strip(); nu = str(u or "").strip()
            dv = re.sub(r"[^0-9]","",nv); du = re.sub(r"[^0-9]","",nu)
            if nv and nu and nv not in ("None","nan") and (nv==nu or (dv and du and dv==du)):
                score += 30
                signals.append("✅ Same CIF/ID (+30)")

    # ── First + Last Name combo ────────────────────────────────────
    fn_a = [n_name(v) for v in get_col(rec_a, col_map, "firstname")]
    fn_b = [n_name(v) for v in get_col(rec_b, col_map, "firstname")]
    ln_a = [n_name(v) for v in get_col(rec_a, col_map, "lastname")]
    ln_b = [n_name(v) for v in get_col(rec_b, col_map, "lastname")]

    # First name match check — handle abbreviations like "M." vs "Mohd"
    fn_match = False
    for fa in fn_a:
        for fb in fn_b:
            if not fa or not fb: continue
            # exact match
            if fa == fb:
                fn_match = True
            # abbreviation: one is single letter, other starts with same letter
            elif (len(fa)==1 and fb.startswith(fa)) or (len(fb)==1 and fa.startswith(fb)):
                fn_match = True

    ln_match = any(la == lb and la for la in ln_a for lb in ln_b)

    # Full name match
    fln_a = [n_name(v) for v in get_col(rec_a, col_map, "fullname")]
    fln_b = [n_name(v) for v in get_col(rec_b, col_map, "fullname")]
    fullname_match = any(fa == fb and fa for fa in fln_a for fb in fln_b)

    if (fn_match and ln_match) or fullname_match:
        score += 25
        signals.append("✅ Same full name (+25)")
    elif fn_match and not ln_match:
        score += 8
        signals.append("⚠️ Same first name only (+8)")
    # last name alone = 0, no signal added

    # ── DOB ────────────────────────────────────────────────────────
    dob_match = False
    for v in get_col(rec_a, col_map, "dob"):
        for u in get_col(rec_b, col_map, "dob"):
            da, db_ = n_dob(v), n_dob(u)
            if da == db_ and da:
                dob_match = True
                score += 20
                signals.append("✅ Same DOB (+20)")
                break
        if dob_match: break

    # ── IP Address ────────────────────────────────────────────────
    for v in get_col(rec_a, col_map, "ip"):
        for u in get_col(rec_b, col_map, "ip"):
            if n_ip(v) == n_ip(u) and n_ip(v) and n_ip(v) not in ("0.0.0.0",""):
                score += 18
                signals.append("✅ Same IP (+18)")

    # ── Address ───────────────────────────────────────────────────
    for v in get_col(rec_a, col_map, "address"):
        for u in get_col(rec_b, col_map, "address"):
            na_, nb_ = n_addr(v), n_addr(u)
            if na_ == nb_ and len(na_) > 4:
                score += 15
                signals.append("✅ Same address (+15)")

    # ── City ──────────────────────────────────────────────────────
    for v in get_col(rec_a, col_map, "city"):
        for u in get_col(rec_b, col_map, "city"):
            if n_name(v) == n_name(u) and n_name(v):
                score += 5
                signals.append("⚠️ Same city (+5)")

    # ── Gender ────────────────────────────────────────────────────
    for v in get_col(rec_a, col_map, "gender"):
        for u in get_col(rec_b, col_map, "gender"):
            if str(v).strip().upper() == str(u).strip().upper() and str(v).strip():
                score += 2  # silent, too weak to show

    return score, list(dict.fromkeys(signals))  # deduplicate signals

# ══════════════════════════════════════════════════════════════════════
# STEP 4 — GEMINI FOR AMBIGUOUS PAIRS ONLY (score 35–79)
# ══════════════════════════════════════════════════════════════════════
def gemini_score_batch(pairs_data, col_map):
    """Send ambiguous pairs to Gemini in one call. Returns {pair_index: decision}."""
    if not pairs_data: return {}

    prompt = f"""You are an MDM expert deciding if customer records are the same real person.

Column types for reference: {json.dumps(col_map)}

Pairs to review:
{json.dumps(pairs_data, default=str, indent=2)}

Rules:
- "M." and "Mohd" = same first name (abbreviation)
- Dates in any format — normalize then compare
- Phone: strip country code (+91), compare last 10 digits
- Address: ignore case, punctuation, spacing
- Same last name alone = NOT a duplicate
- Same city alone = NOT a duplicate
- Need at least 2 independent matching signals

Return ONLY a JSON array:
[
  {{
    "pair_index": <number from input>,
    "decision": "DUPLICATE" or "NOT_DUPLICATE",
    "confidence": <0-100>,
    "reason": "<one sentence why>"
  }}
]"""

    try:
        results = gemini_json(prompt, max_tokens=2000)
        if isinstance(results, list):
            return {r["pair_index"]: r for r in results if "pair_index" in r}
    except:
        pass
    return {}

# ══════════════════════════════════════════════════════════════════════
# STEP 5 — UNION-FIND CLUSTERING
# Groups any number of linked records into one cluster
# ══════════════════════════════════════════════════════════════════════
class UF:
    def __init__(self):
        self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        if self.p[x] != x: self.p[x] = self.find(self.p[x])
        return self.p[x]
    def union(self, x, y):
        self.p[self.find(x)] = self.find(y)
    def get_clusters(self, nodes):
        g = defaultdict(list)
        for n in nodes:
            g[self.find(n)].append(n)
        return [v for v in g.values() if len(v) > 1]

# ══════════════════════════════════════════════════════════════════════
# FULL PIPELINE
# ══════════════════════════════════════════════════════════════════════
def run_full_analysis(all_df):
    cols     = list(all_df.columns)
    all_recs = all_df.to_dict(orient="records")
    n        = len(all_recs)

    status  = st.empty()
    bar     = st.progress(0)
    detail  = st.empty()

    # ── Step 1: Gemini understands table ──────────────────────────
    status.markdown(step_msg("1/5", "Gemini reading table structure..."))
    bar.progress(5)
    id_col, col_map = understand_table(all_df)
    if not id_col: id_col = cols[0]
    st.session_state.col_map = col_map
    st.session_state.id_col  = id_col
    detail.markdown(hint(f"ID: {id_col} | " + " · ".join(f"{c}={t}" for c,t in col_map.items())))

    # ── Step 2: Build candidate pairs via blocking ─────────────────
    status.markdown(step_msg("2/5", "Building candidate pairs via blocking..."))
    bar.progress(20)
    pairs = build_candidate_pairs(all_recs, col_map)
    detail.markdown(hint(f"Total possible pairs: {n*(n-1)//2:,} · Candidate pairs after blocking: {len(pairs)}"))

    # ── Step 3: Python signal scoring ─────────────────────────────
    status.markdown(step_msg("3/5", "Signal scoring all candidate pairs..."))
    bar.progress(35)

    uf          = UF()
    gemini_q    = []   # pairs to send to Gemini
    pair_meta   = {}   # (i,j) -> {score, signals, gemini_result}
    auto_count  = 0
    drop_count  = 0

    for i, j in pairs:
        score, signals = score_pair(all_recs[i], all_recs[j], col_map)

        if score >= 80:
            # High confidence — auto link
            id_i = str(all_recs[i].get(id_col, f"row_{i}"))
            id_j = str(all_recs[j].get(id_col, f"row_{j}"))
            uf.union(id_i, id_j)
            pair_meta[(i,j)] = {"score": score, "signals": signals, "source": "auto"}
            auto_count += 1

        elif score >= 35:
            # Ambiguous — queue for Gemini
            gemini_q.append((i, j, score, signals))
            pair_meta[(i,j)] = {"score": score, "signals": signals, "source": "gemini_pending"}

        else:
            # Not enough evidence — drop silently
            drop_count += 1

    detail.markdown(hint(f"Auto-linked (score≥80): {auto_count} · Gemini queue (35-79): {len(gemini_q)} · Dropped noise (<35): {drop_count}"))

    # ── Step 4: Gemini reviews ambiguous pairs ─────────────────────
    status.markdown(step_msg("4/5", f"Gemini reviewing {len(gemini_q)} ambiguous pairs..."))
    bar.progress(50)

    CHUNK = 10
    for start in range(0, len(gemini_q), CHUNK):
        chunk = gemini_q[start:start+CHUNK]
        batch = []
        for idx, (i, j, score, signals) in enumerate(chunk):
            batch.append({
                "pair_index": start + idx,
                "pre_score":  score,
                "signals_found": signals,
                "record_A":  all_recs[i],
                "record_B":  all_recs[j],
            })

        results = gemini_score_batch(batch, col_map)

        for idx, (i, j, score, signals) in enumerate(chunk):
            res = results.get(start + idx, {})
            if res.get("decision") == "DUPLICATE":
                id_i = str(all_recs[i].get(id_col, f"row_{i}"))
                id_j = str(all_recs[j].get(id_col, f"row_{j}"))
                uf.union(id_i, id_j)
                conf = int(res.get("confidence", score))
                pair_meta[(i,j)].update({
                    "score": max(score, conf),
                    "signals": signals + [f"🤖 Gemini: {res.get('reason','')}"],
                    "source": "gemini_confirmed"
                })
            else:
                pair_meta[(i,j)]["source"] = "gemini_rejected"

        pct = 50 + int(((start+CHUNK)/max(len(gemini_q),1))*30)
        bar.progress(min(pct, 80))

    # ── Step 5: Build clusters ─────────────────────────────────────
    status.markdown(step_msg("5/5", "Building clusters..."))
    bar.progress(85)

    all_ids    = [str(r.get(id_col, f"row_{i}")) for i,r in enumerate(all_recs)]
    id_to_rec  = {str(r.get(id_col, f"row_{i}")): r for i,r in enumerate(all_recs)}
    id_to_idx  = {str(r.get(id_col, f"row_{i}")): i for i,r in enumerate(all_recs)}

    raw_clusters = uf.get_clusters(all_ids)
    clusters = []

    for cids in raw_clusters:
        recs = [id_to_rec[c] for c in cids if c in id_to_rec]
        idxs = [id_to_idx[c] for c in cids if c in id_to_idx]

        # Collect all pair scores within this cluster
        c_scores  = []
        c_signals = []
        for ii, ij in combinations(sorted(idxs), 2):
            key = (min(ii,ij), max(ii,ij))
            if key in pair_meta:
                c_scores.append(pair_meta[key]["score"])
                c_signals.extend(pair_meta[key]["signals"])

        avg_score     = int(sum(c_scores)/len(c_scores)) if c_scores else 50
        unique_signals = list(dict.fromkeys(c_signals))

        clusters.append({
            "cluster_id":   str(uuid.uuid4())[:8].upper(),
            "record_ids":   cids,
            "records":      recs,
            "record_count": len(cids),
            "avg_score":    avg_score,
            "signals":      unique_signals,
        })

    clusters.sort(key=lambda x: x["avg_score"], reverse=True)
    bar.progress(100)
    status.markdown(step_msg("✅ Done", f"Found {len(clusters)} clusters covering {sum(c['record_count'] for c in clusters)} records"))

    return clusters

# ══════════════════════════════════════════════════════════════════════
# UI HELPERS
# ══════════════════════════════════════════════════════════════════════
def step_msg(label, msg):
    return f'<p style="font-family:DM Mono,monospace;font-size:13px;color:#8892b0;"><span style="color:#4f8ef7;font-weight:700;">{label}</span> · {msg}</p>'

def hint(msg):
    return f'<p style="font-family:DM Mono,monospace;font-size:11px;color:#2d3555;margin:4px 0 8px;">{msg}</p>'

def card_open(label):
    return f'<div style="background:#0f1420;border:1px solid #1e2535;border-radius:12px;padding:24px 28px;margin-bottom:16px;"><p style="color:#4f8ef7;font-family:DM Mono,monospace;font-size:11px;text-transform:uppercase;letter-spacing:2px;margin-bottom:16px;">{label}</p>'

def score_pill(score):
    c = "#22c55e" if score>=70 else "#f59e0b" if score>=40 else "#ef4444"
    return f'<span style="background:{c}22;border:1px solid {c}55;border-radius:20px;padding:4px 14px;font-family:DM Mono,monospace;font-size:12px;color:{c};">score {score}</span>'

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

# ══════════════════════════════════════════════════════════════════════
# PAGE 1 — LOGIN
# ══════════════════════════════════════════════════════════════════════
def page_login():
    st.markdown("<br><br>", unsafe_allow_html=True)
    _,c,_ = st.columns([1,1.2,1])
    with c:
        st.markdown("""
        <div style="text-align:center;margin-bottom:32px;">
            <div style="font-size:56px;">❄️</div>
            <div style="font-size:28px;font-weight:800;color:#e8eaf0;margin-top:8px;">MDM Agent</div>
            <div style="font-family:'DM Mono',monospace;font-size:11px;color:#2d3555;
                 margin-top:8px;letter-spacing:3px;">MASTER DATA MANAGEMENT · AI POWERED</div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown('<div style="background:#0f1420;border:1px solid #1e2535;border-radius:14px;padding:32px 36px;">', unsafe_allow_html=True)
        account  = st.text_input("Snowflake Account",  placeholder="xy12345.us-east-1")
        username = st.text_input("Username",            placeholder="your_username")
        password = st.text_input("Password",           type="password", placeholder="••••••••")
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

    st.markdown(card_open("01 · Warehouse & Database"), unsafe_allow_html=True)
    c1,c2 = st.columns(2)
    with c1: wh = st.selectbox("Warehouse", st.session_state.warehouses, key="wh_s")
    with c2: db = st.selectbox("Database",  st.session_state.databases,  key="db_s")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(card_open("02 · Schema"), unsafe_allow_html=True)
    schema = None
    if db:
        if st.session_state.sel_db != db:
            st.session_state.sel_db = db; st.session_state.schemas = []
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

    st.markdown(card_open("03 · Table"), unsafe_allow_html=True)
    table = None
    if schema:
        if st.session_state.sel_schema != schema:
            st.session_state.sel_schema = schema; st.session_state.tables = []
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
        with ca: rpp = st.selectbox("Rows/page",[25,50,100,200],index=1)
        with cb:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button(f"Load  {table}  →"):
                with st.spinner("Loading..."):
                    try:
                        cur = st.session_state.sf_conn.cursor()
                        cur.execute(f'USE WAREHOUSE "{wh}"')
                        cur.close()
                        st.session_state.sel_wh = wh; st.session_state.sel_db = db
                        st.session_state.sel_schema = schema; st.session_state.sel_table = table
                        st.session_state.rows_per_page = rpp
                        st.session_state.total_rows = fetch_count()
                        st.session_state.df = fetch_page(rpp,0)
                        st.session_state.current_page = 1
                        st.session_state.analysis_done = False
                        st.session_state.clusters = []
                        st.session_state.decisions = {}
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
    m1.metric("Total Rows", f"{total:,}")
    m2.metric("Columns",    len(st.session_state.df.columns))
    m3.metric("Page",       f"{pg}/{tp}")
    m4.metric("Rows/Page",  rpp)
    st.markdown("<br>", unsafe_allow_html=True)

    st.dataframe(st.session_state.df, use_container_width=True, height=480, hide_index=True)

    p1,p2,p3,p4,p5 = st.columns([1,1,2,1,1])
    with p1:
        if st.button("⟨⟨ First",disabled=(pg==1),key="bf"):
            st.session_state.current_page=1; st.session_state.df=fetch_page(rpp,0); st.rerun()
    with p2:
        if st.button("⟨ Prev",disabled=(pg==1),key="bp"):
            st.session_state.current_page-=1
            st.session_state.df=fetch_page(rpp,(st.session_state.current_page-1)*rpp); st.rerun()
    with p3:
        st.markdown(f'<div style="text-align:center;padding:10px 0;font-family:DM Mono,monospace;font-size:12px;color:#4b5680;">Rows <b style="color:#4f8ef7">{s:,}–{e:,}</b> of <b style="color:#e8eaf0">{total:,}</b></div>', unsafe_allow_html=True)
    with p4:
        if st.button("Next ⟩",disabled=(pg>=tp),key="bn"):
            st.session_state.current_page+=1
            st.session_state.df=fetch_page(rpp,(st.session_state.current_page-1)*rpp); st.rerun()
    with p5:
        if st.button("Last ⟩⟩",disabled=(pg>=tp),key="bl"):
            st.session_state.current_page=tp
            st.session_state.df=fetch_page(rpp,(tp-1)*rpp); st.rerun()

    j1,j2,_ = st.columns([1,1,4])
    with j1: jump = st.number_input("Jump to page",1,tp,pg,1)
    with j2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Go →",key="bj"):
            st.session_state.current_page=int(jump)
            st.session_state.df=fetch_page(rpp,(int(jump)-1)*rpp); st.rerun()

    st.markdown("---")
    st.markdown(card_open("🤖 MDM Duplicate Detection Agent"), unsafe_allow_html=True)
    st.markdown('<p style="color:#8892b0;font-size:13px;margin-bottom:8px;">Finds clusters of <b style="color:#e8eaf0">any size</b> — 2, 3, 5+ records that are the same person. Uses smart blocking + signal scoring. Gemini only reviews genuinely ambiguous cases.</p>', unsafe_allow_html=True)
    if st.button("🔍 Run MDM Agent →"):
        st.session_state.page="agent"; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════
# PAGE 4 — AGENT
# ══════════════════════════════════════════════════════════════════════
def page_agent():
    topbar("MDM Agent Running", subtitle="Blocking → Scoring → Gemini → Clustering")
    st.markdown("---")
    db,sc,tb = st.session_state.sel_db,st.session_state.sel_schema,st.session_state.sel_table

    if not st.session_state.analysis_done:
        st.markdown('<div style="background:#0f1420;border:1px solid #1e2535;border-radius:14px;padding:32px;">', unsafe_allow_html=True)
        all_df   = sf_query(f'SELECT * FROM "{db}"."{sc}"."{tb}"')
        clusters = run_full_analysis(all_df)
        st.session_state.clusters      = clusters
        st.session_state.analysis_done = True
        st.session_state.decisions     = {c["cluster_id"]:"PENDING" for c in clusters}
        st.markdown("</div>", unsafe_allow_html=True)
        st.rerun()
    else:
        clusters   = st.session_state.clusters
        total_recs = sum(c["record_count"] for c in clusters)
        st.success(f"✅ Found **{len(clusters)} duplicate clusters** covering **{total_recs} records**")
        m1,m2,m3 = st.columns(3)
        m1.metric("Clusters",       len(clusters))
        m2.metric("Records Affected",total_recs)
        m3.metric("To Merge",        total_recs - len(clusters))
        if st.session_state.col_map:
            with st.expander("🧠 How Gemini understood your table"):
                st.json(st.session_state.col_map)
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
    topbar("Human Review", subtitle="Review each cluster — approve or reject the merge")
    st.markdown("---")

    clusters  = st.session_state.clusters
    decisions = st.session_state.decisions

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

    st.markdown("<br>", unsafe_allow_html=True)
    ba,bb,bc = st.columns(3)
    with ba: thresh = st.slider("Bulk approve — min score", 30, 100, 70)
    with bb:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button(f"✅ Approve all ≥ {thresh}"):
            for c in clusters:
                if c["avg_score"] >= thresh:
                    decisions[c["cluster_id"]] = "APPROVED"
            st.rerun()
    with bc:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("❌ Reject all < 40"):
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
            st.markdown('<p style="color:#4b5680;font-family:DM Mono,monospace;font-size:13px;padding:20px 0;">Nothing here.</p>', unsafe_allow_html=True)
            return

        for cluster in shown:
            cid    = cluster["cluster_id"]
            score  = cluster["avg_score"]
            recs   = cluster["records"]
            signals= cluster["signals"]
            n_recs = cluster["record_count"]

            sc_col = "#22c55e" if score>=70 else "#f59e0b" if score>=40 else "#ef4444"

            # ── Cluster card ──────────────────────────────────────
            st.markdown(f"""
            <div style="background:#0f1420;border:1px solid #1e2535;
                 border-radius:14px;padding:20px 24px;margin-bottom:8px;">
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px;">
                    <span style="font-family:DM Mono,monospace;font-size:11px;color:#4b5680;">
                        CLUSTER #{cid}
                    </span>
                    {score_pill(score)}
                    <span style="background:#1e2535;border-radius:20px;padding:4px 14px;
                          font-family:DM Mono,monospace;font-size:11px;color:#8892b0;">
                        {n_recs} records
                    </span>
                </div>
                <div style="background:#07090f;border-radius:8px;padding:10px 14px;margin-bottom:6px;">
                    <div style="font-family:DM Mono,monospace;font-size:10px;color:#4b5680;
                         text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">
                         Evidence
                    </div>
                    <div style="display:flex;flex-wrap:wrap;gap:6px;">
                        {"".join([f'<span style="background:#1e2535;border-radius:4px;padding:3px 10px;font-family:DM Mono,monospace;font-size:11px;color:#8892b0;">{s}</span>' for s in signals[:8]])}
                    </div>
                    <div style="margin-top:10px;background:#1e2535;border-radius:4px;height:6px;">
                        <div style="background:{sc_col};width:{min(score,100)}%;height:6px;border-radius:4px;"></div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # ── Records side by side ──────────────────────────────
            if recs:
                all_keys = list(recs[0].keys())
                id_col   = st.session_state.id_col or all_keys[0]
                pal      = ["#4f8ef7","#7c5ce8","#22c55e","#f59e0b",
                            "#ef4444","#06b6d4","#ec4899","#84cc16"]
                cols_ui  = st.columns(n_recs)

                for ri, rec in enumerate(recs):
                    rid = str(rec.get(id_col,"?"))
                    cc  = pal[ri % len(pal)]
                    with cols_ui[ri]:
                        st.markdown(f'<div style="background:#07090f;border:1px solid #1e2535;border-radius:10px;padding:14px;">', unsafe_allow_html=True)
                        st.markdown(f'<p style="color:{cc};font-family:DM Mono,monospace;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;">#{ri+1} · {rid}</p>', unsafe_allow_html=True)
                        for k in all_keys:
                            val      = str(rec.get(k,"") or "")
                            all_vals = [str(r.get(k,"") or "").lower().strip() for r in recs]
                            matching = len(set(v for v in all_vals if v)) == 1
                            fc = "#22c55e" if matching else "#e8eaf0"
                            st.markdown(f"""
                            <div style="margin-bottom:7px;padding-bottom:7px;border-bottom:1px solid #1e2535;">
                                <div style="font-family:DM Mono,monospace;font-size:9px;color:#2d3555;
                                     text-transform:uppercase;margin-bottom:2px;">{k}</div>
                                <div style="font-size:12px;color:{fc};">{val or "—"}</div>
                            </div>""", unsafe_allow_html=True)
                        st.markdown("</div>", unsafe_allow_html=True)

                st.markdown('<p style="font-family:DM Mono,monospace;font-size:10px;color:#2d3555;margin:6px 0 4px;">🟢 Green = all records share the same value</p>', unsafe_allow_html=True)

            # ── Action buttons ────────────────────────────────────
            if filter_status == "PENDING":
                b1,b2,_ = st.columns([2,2,3])
                with b1:
                    if st.button("✅ Approve — merge all", key=f"ap_{cid}"):
                        st.session_state.decisions[cid]="APPROVED"; st.rerun()
                with b2:
                    if st.button("❌ Reject — keep separate", key=f"rj_{cid}"):
                        st.session_state.decisions[cid]="REJECTED"; st.rerun()
            else:
                if st.button("↩ Undo", key=f"ud_{cid}"):
                    st.session_state.decisions[cid]="PENDING"; st.rerun()

            st.markdown("<br>", unsafe_allow_html=True)

    with tab1: render_clusters("PENDING")
    with tab2: render_clusters("APPROVED")
    with tab3: render_clusters("REJECTED")

    if approved > 0:
        st.markdown("---")
        st.markdown(card_open(f"🏆 {approved} cluster(s) approved — ready to build golden records"), unsafe_allow_html=True)
        st.markdown(f'<p style="color:#8892b0;font-size:13px;margin-bottom:16px;">Creates <b style="color:#e8eaf0">{st.session_state.sel_table}_GOLDEN</b> and <b style="color:#e8eaf0">{st.session_state.sel_table}_MDM_AUDIT</b> in your schema.</p>', unsafe_allow_html=True)
        if st.button("🏆 Build Golden Records & Write to Snowflake →"):
            st.session_state.page="golden"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════
# PAGE 6 — GOLDEN RECORD
# ══════════════════════════════════════════════════════════════════════
def page_golden():
    topbar("Golden Record Builder", subtitle="Merging approved clusters into master records")
    st.markdown("---")

    db,sc,tb  = st.session_state.sel_db,st.session_state.sel_schema,st.session_state.sel_table
    clusters  = st.session_state.clusters
    decisions = st.session_state.decisions
    approved  = [c for c in clusters if decisions.get(c["cluster_id"])=="APPROVED"]
    id_col    = st.session_state.id_col or "ID"

    st.markdown('<div style="background:#0f1420;border:1px solid #1e2535;border-radius:14px;padding:32px;">', unsafe_allow_html=True)
    status = st.empty()
    bar    = st.progress(0)

    status.markdown(step_msg("1/3","Applying survivorship — picking best value per field..."))
    bar.progress(20)

    # Survivorship: longest/most complete value wins
    def best_val(vals):
        clean = [v for v in vals if v and str(v).strip() not in ("","None","nan","NULL")]
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
        golden["SIGNALS"]      = " | ".join(cluster["signals"][:5])
        golden["CREATED_AT"]   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        golden_rows.append(golden)

    bar.progress(50)
    status.markdown(step_msg("2/3","Writing golden table to Snowflake..."))

    golden_tb = f"{tb}_GOLDEN"
    audit_tb  = f"{tb}_MDM_AUDIT"
    golden_df = pd.DataFrame(golden_rows)

    try:
        col_defs = [f'"{c}" NUMBER' if c in ("MERGED_COUNT","MATCH_SCORE") else f'"{c}" VARCHAR'
                    for c in golden_df.columns]
        sf_write(f'CREATE OR REPLACE TABLE "{db}"."{sc}"."{golden_tb}" ({", ".join(col_defs)})')

        for _,row in golden_df.iterrows():
            vals = ["NULL" if (v is None or str(v).strip() in ("","nan"))
                    else f"'{str(v).replace(chr(39),chr(39)*2)}'"
                    for v in row.values]
            sf_write(f'INSERT INTO "{db}"."{sc}"."{golden_tb}" VALUES ({", ".join(vals)})')

        sf_write(f"""CREATE OR REPLACE TABLE "{db}"."{sc}"."{audit_tb}" (
            CLUSTER_ID VARCHAR, RECORD_IDS VARCHAR, RECORD_COUNT NUMBER,
            MATCH_SCORE NUMBER, SIGNALS VARCHAR, HUMAN_DECISION VARCHAR, REVIEWED_AT VARCHAR)""")

        for cluster in clusters:
            d    = decisions.get(cluster["cluster_id"],"PENDING")
            ids  = ", ".join(cluster["record_ids"])
            sigs = " | ".join(cluster["signals"][:5]).replace("'","''")
            sf_write(f"""INSERT INTO "{db}"."{sc}"."{audit_tb}" VALUES (
                '{cluster["cluster_id"]}','{ids}',{cluster["record_count"]},
                {cluster["avg_score"]},'{sigs}','{d}',
                '{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')""")

        bar.progress(100)
        status.markdown(step_msg("✅ Done","All records written to Snowflake!"))
        st.markdown("</div>", unsafe_allow_html=True)

        st.success(f"✅ **{db}.{sc}.{golden_tb}** — {len(golden_rows)} golden master records")
        st.success(f"✅ **{db}.{sc}.{audit_tb}** — audit trail for all {len(clusters)} clusters")

        st.markdown("### Preview")
        st.dataframe(golden_df, use_container_width=True, hide_index=True)

        st.markdown("### Verify in Snowflake")
        st.code(f"""-- Golden records
SELECT * FROM "{db}"."{sc}"."{golden_tb}";

-- Full audit trail
SELECT * FROM "{db}"."{sc}"."{audit_tb}" ORDER BY MATCH_SCORE DESC;

-- Summary
SELECT HUMAN_DECISION, COUNT(*), AVG(MATCH_SCORE)
FROM "{db}"."{sc}"."{audit_tb}" GROUP BY 1;""", language="sql")

        if st.button("← Back to Review"):
            st.session_state.page="hitl"; st.rerun()

    except Exception as e:
        st.markdown("</div>", unsafe_allow_html=True)
        st.error(f"Error writing to Snowflake: {e}")
        if st.button("← Back"):
            st.session_state.page="hitl"; st.rerun()

# ── Router ─────────────────────────────────────────────────────────────────────
pg = st.session_state.page
if not st.session_state.sf_conn and pg != "login":
    st.session_state.page = "login"; pg = "login"

if   pg == "login":  page_login()
elif pg == "setup":  page_setup()
elif pg == "table":  page_table()
elif pg == "agent":  page_agent()
elif pg == "hitl":   page_hitl()
elif pg == "golden": page_golden()
