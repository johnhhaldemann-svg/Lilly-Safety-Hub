import re
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

# DB
from psycopg2.extras import RealDictCursor

# Supabase Storage
from supabase import create_client

# =========================
# SETTINGS
# =========================
APP_TITLE = "Lilly Safety Hub"

# Local fallback (only used if Supabase secrets are missing)
BASE_DIR = Path(__file__).parent.resolve()
LOCAL_DATA_DIR = BASE_DIR / "violation_data"
LOCAL_DB_PATH = LOCAL_DATA_DIR / "lilly_safety_hub.db"
LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)

REPEAT_THRESHOLD_TOTAL = 3
REPEAT_THRESHOLD_30D = 2


# =========================
# AUTH (Password)
# =========================
def get_app_password() -> str:
    return str(st.secrets.get("APP_PASSWORD", "ChangeMe123!"))


def ensure_logged_in():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False

    if st.session_state.logged_in:
        return

    st.title(APP_TITLE)
    st.subheader("Login")

    pw = st.text_input("Password", type="password")
    if st.button("Login"):
        if pw == get_app_password():
            st.session_state.logged_in = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()


# =========================
# SUPABASE + DB CONFIG
# =========================
def supabase_enabled() -> bool:
    return all(k in st.secrets for k in ["SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_BUCKET", "DATABASE_URL"])


def get_bucket_name() -> str:
    return str(st.secrets.get("SUPABASE_BUCKET", "evidence"))


@st.cache_resource
def get_supabase_client():
    url = str(st.secrets["SUPABASE_URL"])
    key = str(st.secrets["SUPABASE_SERVICE_KEY"])
    return create_client(url, key)


def db_connect():
    """
    Uses Supabase Postgres if configured; otherwise local SQLite fallback.
    """
    if supabase_enabled():
        return psycopg2.connect(str(st.secrets["DATABASE_URL"]), cursor_factory=RealDictCursor)
    else:
        import sqlite3

        conn = sqlite3.connect(LOCAL_DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def db_is_postgres(conn) -> bool:
    return conn.__class__.__module__.startswith("psycopg2")


# =========================
# DB INIT
# =========================
def init_db():
    conn = db_connect()
    try:
        if db_is_postgres(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS personnel_violations (
                        id BIGSERIAL PRIMARY KEY,
                        created_at TEXT,
                        date_of_event TEXT,
                        hard_hat_number TEXT,
                        company TEXT,
                        trade TEXT,
                        location TEXT,
                        violation_type TEXT,
                        severity TEXT,
                        description TEXT,
                        corrective_action TEXT,
                        evidence_path TEXT
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS site_issues (
                        id BIGSERIAL PRIMARY KEY,
                        created_at TEXT,
                        date_of_event TEXT,
                        company TEXT,
                        building TEXT,
                        floor TEXT,
                        risk_level TEXT,
                        issue TEXT,
                        photo_path TEXT
                    );
                    """
                )
                conn.commit()
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS personnel_violations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT,
                    date_of_event TEXT,
                    hard_hat_number TEXT,
                    company TEXT,
                    trade TEXT,
                    location TEXT,
                    violation_type TEXT,
                    severity TEXT,
                    description TEXT,
                    corrective_action TEXT,
                    evidence_path TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS site_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT,
                    date_of_event TEXT,
                    company TEXT,
                    building TEXT,
                    floor TEXT,
                    risk_level TEXT,
                    issue TEXT,
                    photo_path TEXT
                )
                """
            )
            conn.commit()
    finally:
        conn.close()


# =========================
# HELPERS
# =========================
def clean_token(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "", (text or "").strip())


def safe_filename(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", name or "file")


def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def dict_row(r):
    return r if isinstance(r, dict) else dict(r)


# =========================
# STORAGE (Supabase Storage)
# =========================
def upload_evidence(uploaded_file, folder: str) -> str | None:
    """
    Uploads file to Supabase Storage bucket and returns the storage path.
    Falls back to local storage if Supabase not configured.
    """
    if not uploaded_file:
        return None

    filename = f"{folder}/{now_ts()}_{uuid.uuid4().hex}_{safe_filename(uploaded_file.name)}"

    # If Supabase is available, upload to bucket
    if supabase_enabled():
        sb = get_supabase_client()
        bucket = get_bucket_name()
        data = uploaded_file.getvalue()

        # supabase-py upload: (path, file, file_options={...})
        sb.storage.from_(bucket).upload(
            filename,
            data,
            file_options={"content-type": uploaded_file.type or "application/octet-stream", "upsert": "true"},
        )
        return filename

    # Local fallback (not permanent on Streamlit Cloud)
    local_dir = LOCAL_DATA_DIR / "uploads"
    local_dir.mkdir(parents=True, exist_ok=True)
    out = local_dir / f"{now_ts()}_{safe_filename(uploaded_file.name)}"
    with open(out, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return str(out)


def make_signed_url(storage_path: str, seconds: int = 3600) -> str | None:
    """
    For private bucket: returns a signed URL so you can open the file on phone.
    """
    if not storage_path or not supabase_enabled():
        return None

    sb = get_supabase_client()
    bucket = get_bucket_name()
    res = sb.storage.from_(bucket).create_signed_url(storage_path, seconds)

    if isinstance(res, dict) and "signedURL" in res:
        return res["signedURL"]
    if hasattr(res, "get") and res.get("signedURL"):
        return res.get("signedURL")
    return None


# =========================
# DB OPS — Personnel
# =========================
def insert_personnel(row: dict):
    conn = db_connect()
    try:
        if db_is_postgres(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO personnel_violations (
                        created_at, date_of_event, hard_hat_number,
                        company, trade, location, violation_type, severity,
                        description, corrective_action, evidence_path
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        row["created_at"],
                        row["date_of_event"],
                        row["hard_hat_number"],
                        row.get("company"),
                        row.get("trade"),
                        row.get("location"),
                        row["violation_type"],
                        row["severity"],
                        row["description"],
                        row.get("corrective_action"),
                        row.get("evidence_path"),
                    ),
                )
                conn.commit()
        else:
            conn.execute(
                """
                INSERT INTO personnel_violations (
                    created_at, date_of_event, hard_hat_number,
                    company, trade, location, violation_type, severity,
                    description, corrective_action, evidence_path
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row["created_at"],
                    row["date_of_event"],
                    row["hard_hat_number"],
                    row.get("company"),
                    row.get("trade"),
                    row.get("location"),
                    row["violation_type"],
                    row["severity"],
                    row["description"],
                    row.get("corrective_action"),
                    row.get("evidence_path"),
                ),
            )
            conn.commit()
    finally:
        conn.close()


def count_personnel(hard_hat: str):
    conn = db_connect()
    try:
        if db_is_postgres(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM personnel_violations WHERE hard_hat_number=%s", (hard_hat,))
                total = int(cur.fetchone()["c"])

                cur.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM personnel_violations
                    WHERE hard_hat_number=%s
                      AND (date_of_event::date >= (now()::date - interval '30 days'))
                    """,
                    (hard_hat,),
                )
                last30 = int(cur.fetchone()["c"])
        else:
            total = conn.execute(
                "SELECT COUNT(*) c FROM personnel_violations WHERE hard_hat_number=?",
                (hard_hat,),
            ).fetchone()["c"]

            last30 = conn.execute(
                """
                SELECT COUNT(*) c
                FROM personnel_violations
                WHERE hard_hat_number=?
                  AND date(date_of_event) >= date('now','-30 day')
                """,
                (hard_hat,),
            ).fetchone()["c"]
        return total, last30
    finally:
        conn.close()


def fetch_hardhats():
    conn = db_connect()
    try:
        if db_is_postgres(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT hard_hat_number FROM personnel_violations ORDER BY hard_hat_number")
                return [r["hard_hat_number"] for r in cur.fetchall() if r["hard_hat_number"]]
        else:
            rows = conn.execute("SELECT DISTINCT hard_hat_number FROM personnel_violations ORDER BY hard_hat_number").fetchall()
            return [r["hard_hat_number"] for r in rows if r["hard_hat_number"]]
    finally:
        conn.close()


def fetch_personnel(filters: dict):
    where = []
    params = []

    if filters.get("hard_hat") and filters["hard_hat"] != "(All)":
        where.append("hard_hat_number = %s" if supabase_enabled() else "hard_hat_number = ?")
        params.append(filters["hard_hat"])
    if filters.get("type") and filters["type"] != "(All)":
        where.append("violation_type = %s" if supabase_enabled() else "violation_type = ?")
        params.append(filters["type"])
    if filters.get("severity") and filters["severity"] != "(All)":
        where.append("severity = %s" if supabase_enabled() else "severity = ?")
        params.append(filters["severity"])
    if filters.get("keyword"):
        like_expr = "%s" if supabase_enabled() else "?"
        where.append(f"(description LIKE {like_expr} OR location LIKE {like_expr} OR company LIKE {like_expr} OR trade LIKE {like_expr})")
        k = f"%{filters['keyword']}%"
        params.extend([k, k, k, k])

    clause = " AND ".join(where) if where else "1=1"
    sql = f"SELECT * FROM personnel_violations WHERE {clause} ORDER BY created_at DESC"

    conn = db_connect()
    try:
        if db_is_postgres(conn):
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict_row(r) for r in cur.fetchall()]
        else:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict_row(r) for r in rows]
    finally:
        conn.close()


# =========================
# DB OPS — Site Issues
# =========================
def insert_site(row: dict):
    conn = db_connect()
    try:
        if db_is_postgres(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO site_issues (
                        created_at, date_of_event, company,
                        building, floor, risk_level, issue, photo_path
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        row["created_at"],
                        row["date_of_event"],
                        row["company"],
                        row["building"],
                        row["floor"],
                        row["risk_level"],
                        row["issue"],
                        row.get("photo_path"),
                    ),
                )
                conn.commit()
        else:
            conn.execute(
                """
                INSERT INTO site_issues (
                    created_at, date_of_event, company,
                    building, floor, risk_level, issue, photo_path
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    row["created_at"],
                    row["date_of_event"],
                    row["company"],
                    row["building"],
                    row["floor"],
                    row["risk_level"],
                    row["issue"],
                    row.get("photo_path"),
                ),
            )
            conn.commit()
    finally:
        conn.close()


def fetch_buildings():
    conn = db_connect()
    try:
        if db_is_postgres(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT building FROM site_issues ORDER BY building")
                return [r["building"] for r in cur.fetchall() if r["building"]]
        else:
            rows = conn.execute("SELECT DISTINCT building FROM site_issues ORDER BY building").fetchall()
            return [r["building"] for r in rows if r["building"]]
    finally:
        conn.close()


def fetch_site(filters: dict):
    where = []
    params = []

    if filters.get("building") and filters["building"] != "(All)":
        where.append("building = %s" if supabase_enabled() else "building = ?")
        params.append(filters["building"])
    if filters.get("floor") and filters["floor"] != "(All)":
        where.append("floor = %s" if supabase_enabled() else "floor = ?")
        params.append(filters["floor"])
    if filters.get("risk_level") and filters["risk_level"] != "(All)":
        where.append("risk_level = %s" if supabase_enabled() else "risk_level = ?")
        params.append(filters["risk_level"])
    if filters.get("company_contains"):
        like_expr = "%s" if supabase_enabled() else "?"
        where.append(f"company LIKE {like_expr}")
        params.append(f"%{filters['company_contains']}%")
    if filters.get("keyword"):
        like_expr = "%s" if supabase_enabled() else "?"
        where.append(f"issue LIKE {like_expr}")
        params.append(f"%{filters['keyword']}%")

    clause = " AND ".join(where) if where else "1=1"
    sql = f"SELECT * FROM site_issues WHERE {clause} ORDER BY created_at DESC"

    conn = db_connect()
    try:
        if db_is_postgres(conn):
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict_row(r) for r in cur.fetchall()]
        else:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict_row(r) for r in rows]
    finally:
        conn.close()


# =========================
# APP START
# =========================
st.set_page_config(page_title=APP_TITLE, layout="wide")
ensure_logged_in()
init_db()

top = st.columns([4, 1])
with top[0]:
    st.title(APP_TITLE)
with top[1]:
    if st.button("Logout"):
        st.session_state.logged_in = False
        st.rerun()

if supabase_enabled():
    st.success("Permanent storage: ON (Supabase DB + Storage)")
else:
    st.warning("Permanent storage: OFF (local fallback). Add Supabase + DATABASE_URL secrets in Streamlit Cloud.")

mode = st.radio(
    "Select Entry Type",
    ["Personnel Safety Violation (Hard Hat #)", "Site Safety Issue (Building/Floor)"],
    horizontal=True,
)

st.divider()

# =========================
# PERSONNEL
# =========================
if mode == "Personnel Safety Violation (Hard Hat #)":
    tab_log, tab_review = st.tabs(["Log Personnel Violation", "Review / Search"])

    with tab_log:
        st.subheader("Personnel Safety Violation")

        c1, c2, c3 = st.columns(3)
        with c1:
            hh_raw = st.text_input("Hard Hat Number *", placeholder="Example: 117")
            company = st.text_input("Company (optional)")
            trade = st.text_input("Trade (optional)")
        with c2:
            date_event = st.date_input("Date of Event *")
            location = st.text_input("Location / Area (optional)")
            v_type = st.selectbox(
                "Violation Type *",
                [
                    "PPE",
                    "Fall Protection",
                    "Lift / AWP",
                    "Scaffold",
                    "Housekeeping",
                    "Electrical",
                    "Hot Work",
                    "Rigging",
                    "LOTO",
                    "Excavation/Trenching",
                    "Traffic Control",
                    "Tools/Equipment",
                    "Other",
                ],
            )
        with c3:
            severity = st.selectbox("Severity *", ["Low", "Medium", "High", "Critical"])
            evidence = st.file_uploader("Upload Evidence (optional)")

        description = st.text_area("What happened? *", placeholder="Clear, objective description.")
        corrective_action = st.text_area("Corrective Action / Coaching (optional)")

        hh = clean_token(hh_raw)

        if hh:
            total, last30 = count_personnel(hh)
            st.caption(f"History for HH#{hh} → Total: {total} | Last 30 days: {last30}")
            if total >= REPEAT_THRESHOLD_TOTAL or last30 >= REPEAT_THRESHOLD_30D:
                st.warning("⚠️ Repeat offender threshold hit — consider escalation.")

        if st.button("Save Personnel Violation"):
            if not hh:
                st.error("Hard Hat Number is required.")
            elif not description.strip():
                st.error("Description is required.")
            else:
                evidence_path = None
                if evidence:
                    # Stores in: evidence / people/<hardhat>/...
                    evidence_path = upload_evidence(evidence, f"people/{hh}")

                insert_personnel(
                    {
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "date_of_event": date_event.isoformat(),
                        "hard_hat_number": hh,
                        "company": company.strip() or None,
                        "trade": trade.strip() or None,
                        "location": location.strip() or None,
                        "violation_type": v_type,
                        "severity": severity,
                        "description": description.strip(),
                        "corrective_action": corrective_action.strip() or None,
                        "evidence_path": evidence_path,
                    }
                )

                st.success(f"Saved personnel violation for HH#{hh}")

                if evidence_path and supabase_enabled():
                    url = make_signed_url(evidence_path)
                    if url:
                        st.link_button("Open evidence (signed link)", url)

    with tab_review:
        st.subheader("Review / Search — Personnel Violations")

        col1, col2, col3, col4 = st.columns(4)
        hardhats = ["(All)"] + fetch_hardhats()
        with col1:
            hh_pick = st.selectbox("Hard Hat #", hardhats)
        with col2:
            type_pick = st.selectbox(
                "Type",
                ["(All)", "PPE", "Fall Protection", "Lift / AWP", "Scaffold", "Housekeeping", "Electrical", "Hot Work",
                 "Rigging", "LOTO", "Excavation/Trenching", "Traffic Control", "Tools/Equipment", "Other"],
            )
        with col3:
            sev_pick = st.selectbox("Severity", ["(All)", "Low", "Medium", "High", "Critical"])
        with col4:
            keyword = st.text_input("Keyword (desc/location/company/trade)")

        rows = fetch_personnel(
            {
                "hard_hat": hh_pick,
                "type": type_pick,
                "severity": sev_pick,
                "keyword": keyword.strip() if keyword.strip() else None,
            }
        )

        st.write(f"Results: **{len(rows)}**")
        for d in rows:
            title = f"{d.get('date_of_event','')} | HH#{d.get('hard_hat_number','')} | {d.get('violation_type','')} | {d.get('severity','')}"
            with st.expander(title):
                st.write(f"**Location:** {d.get('location') or '—'}")
                st.write(f"**Company / Trade:** {d.get('company') or '—'} / {d.get('trade') or '—'}")
                st.write(f"**Description:** {d.get('description')}")
                st.write(f"**Corrective Action:** {d.get('corrective_action') or '—'}")

                if d.get("evidence_path") and supabase_enabled():
                    url = make_signed_url(d["evidence_path"])
                    if url:
                        st.link_button("Open evidence (signed link)", url)
                    st.code(d["evidence_path"])

# =========================
# SITE ISSUES
# =========================
else:
    tab_log, tab_review = st.tabs(["Log Site Issue", "Review / Search"])

    with tab_log:
        st.subheader("Site Safety Issue")

        c1, c2, c3 = st.columns(3)
        with c1:
            company = st.text_input("Company Responsible *", placeholder="Example: ABC Electric")
            building = st.text_input("Building *", placeholder="Example: West Addition")
        with c2:
            floor = st.text_input("Floor *", placeholder="Example: 1, 2, Roof")
            date_event = st.date_input("Date Observed *")
        with c3:
            risk_level = st.selectbox("Risk Level *", ["Low", "Medium", "High", "Critical"])
            photo = st.file_uploader("Upload Photo (optional)")

        issue = st.text_area("Describe the Issue *")

        if st.button("Save Site Safety Issue"):
            if not company.strip():
                st.error("Company is required.")
            elif not building.strip():
                st.error("Building is required.")
            elif not floor.strip():
                st.error("Floor is required.")
            elif not issue.strip():
                st.error("Issue description is required.")
            else:
                photo_path = None
                if photo:
                    # Stores in: evidence / site/<building>/floor_<floor>/...
                    b = safe_filename(building.strip())
                    f = safe_filename(floor.strip())
                    photo_path = upload_evidence(photo, f"site/{b}/floor_{f}")

                insert_site(
                    {
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "date_of_event": date_event.isoformat(),
                        "company": company.strip(),
                        "building": building.strip(),
                        "floor": floor.strip(),
                        "risk_level": risk_level,
                        "issue": issue.strip(),
                        "photo_path": photo_path,
                    }
                )

                st.success("Saved site safety issue")

                if photo_path and supabase_enabled():
                    url = make_signed_url(photo_path)
                    if url:
                        st.link_button("Open photo (signed link)", url)

    with tab_review:
        st.subheader("Review / Search — Site Safety Issues")

        col1, col2, col3, col4 = st.columns(4)
        buildings = ["(All)"] + fetch_buildings()

        with col1:
            building_pick = st.selectbox("Building", buildings)
        with col2:
            floor_pick = st.selectbox("Floor", ["(All)", "1", "2", "3", "4", "5", "Mezz", "Roof", "Basement", "Other"])
        with col3:
            risk_pick = st.selectbox("Risk Level", ["(All)", "Low", "Medium", "High", "Critical"])
        with col4:
            company_contains = st.text_input("Company contains")
            keyword = st.text_input("Issue keyword")

        rows = fetch_site(
            {
                "building": building_pick,
                "floor": floor_pick,
                "risk_level": risk_pick,
                "company_contains": company_contains.strip() if company_contains.strip() else None,
                "keyword": keyword.strip() if keyword.strip() else None,
            }
        )

        st.write(f"Results: **{len(rows)}**")
        for d in rows:
            title = f"{d.get('date_of_event','')} | {d.get('company','—')} | {d.get('building','—')} | Floor {d.get('floor','—')} | Risk {d.get('risk_level','—')}"
            with st.expander(title):
                st.write(f"**Issue:** {d.get('issue')}")
                if d.get("photo_path") and supabase_enabled():
                    url = make_signed_url(d["photo_path"])
                    if url:
                        st.link_button("Open photo (signed link)", url)
                    st.code(d["photo_path"])
