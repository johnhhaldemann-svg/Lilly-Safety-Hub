import re
import sqlite3
from datetime import datetime
from pathlib import Path

import streamlit as st

# =========================
# SETTINGS
# =========================
APP_TITLE = "Lilly Safety Hub"
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "violation_data"

PEOPLE_DIR = DATA_DIR / "people"
SITE_DIR = DATA_DIR / "site_issues"
SITE_EVIDENCE_DIR = SITE_DIR / "evidence"

DB_PATH = DATA_DIR / "lilly_safety_hub.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
SITE_DIR.mkdir(parents=True, exist_ok=True)
SITE_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

REPEAT_THRESHOLD_TOTAL = 3
REPEAT_THRESHOLD_30D = 2

# =========================
# AUTH
# =========================
def get_app_password() -> str:
    # Best practice: Streamlit Secrets
    # Local: .streamlit/secrets.toml with APP_PASSWORD="..."
    # Cloud: Settings -> Secrets
    if "APP_PASSWORD" in st.secrets:
        return str(st.secrets["APP_PASSWORD"])
    # Fallback for local testing ONLY
    return "ChangeMe123!"

def ensure_logged_in():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False

    if st.session_state.logged_in:
        return

    st.title(APP_TITLE)
    st.subheader("Login")
    st.caption("Enter the site password to access the Safety Hub.")
    pw = st.text_input("Password", type="password")

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("Login"):
            if pw == get_app_password():
                st.session_state.logged_in = True
                st.success("Logged in.")
                st.rerun()
            else:
                st.error("Wrong password.")

    st.stop()

# =========================
# HELPERS
# =========================
def clean_token(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "", (text or "").strip())

def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def table_columns(conn, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {r["name"] for r in rows}

def ensure_column(conn, table: str, col: str, col_type: str):
    cols = table_columns(conn, table)
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")

def init_db_and_migrate():
    with db_conn() as conn:
        conn.execute("""
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
        """)

        conn.execute("""
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
        """)

        for col, typ in [
            ("created_at", "TEXT"), ("date_of_event", "TEXT"), ("hard_hat_number", "TEXT"),
            ("company", "TEXT"), ("trade", "TEXT"), ("location", "TEXT"),
            ("violation_type", "TEXT"), ("severity", "TEXT"), ("description", "TEXT"),
            ("corrective_action", "TEXT"), ("evidence_path", "TEXT")
        ]:
            ensure_column(conn, "personnel_violations", col, typ)

        for col, typ in [
            ("created_at", "TEXT"), ("date_of_event", "TEXT"), ("company", "TEXT"),
            ("building", "TEXT"), ("floor", "TEXT"), ("risk_level", "TEXT"),
            ("issue", "TEXT"), ("photo_path", "TEXT")
        ]:
            ensure_column(conn, "site_issues", col, typ)

        conn.commit()

def person_folder(hard_hat: str) -> Path:
    folder = PEOPLE_DIR / clean_token(hard_hat)
    (folder / "evidence").mkdir(parents=True, exist_ok=True)
    return folder

def save_evidence_personnel(hard_hat: str, uploaded_file):
    if not uploaded_file:
        return None
    folder = person_folder(hard_hat) / "evidence"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", uploaded_file.name)
    path = folder / f"{ts}_{safe}"
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return str(path)

def save_evidence_site(uploaded_file):
    if not uploaded_file:
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", uploaded_file.name)
    path = SITE_EVIDENCE_DIR / f"{ts}_{safe}"
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return str(path)

# =========================
# DB OPS — Personnel
# =========================
def insert_personnel_violation(row: dict):
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO personnel_violations (
                created_at, date_of_event, hard_hat_number,
                company, trade, location,
                violation_type, severity, description,
                corrective_action, evidence_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row["created_at"], row["date_of_event"], row["hard_hat_number"],
            row.get("company"), row.get("trade"), row.get("location"),
            row["violation_type"], row["severity"], row["description"],
            row.get("corrective_action"), row.get("evidence_path")
        ))
        conn.commit()

def count_personnel_violations(hard_hat: str):
    with db_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) c FROM personnel_violations WHERE hard_hat_number = ?",
            (hard_hat,)
        ).fetchone()["c"]

        last_30 = conn.execute("""
            SELECT COUNT(*) c
            FROM personnel_violations
            WHERE hard_hat_number = ?
              AND date(date_of_event) >= date('now','-30 day')
        """, (hard_hat,)).fetchone()["c"]
    return total, last_30

def fetch_hardhats():
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT hard_hat_number FROM personnel_violations ORDER BY hard_hat_number"
        ).fetchall()
    return [r["hard_hat_number"] for r in rows]

def fetch_personnel(filters: dict):
    where, params = [], []
    if filters.get("hard_hat") and filters["hard_hat"] != "(All)":
        where.append("hard_hat_number = ?")
        params.append(filters["hard_hat"])
    if filters.get("type") and filters["type"] != "(All)":
        where.append("violation_type = ?")
        params.append(filters["type"])
    if filters.get("severity") and filters["severity"] != "(All)":
        where.append("severity = ?")
        params.append(filters["severity"])
    if filters.get("keyword"):
        where.append("(description LIKE ? OR location LIKE ? OR company LIKE ? OR trade LIKE ?)")
        k = f"%{filters['keyword']}%"
        params.extend([k, k, k, k])

    sql = " AND ".join(where) if where else "1=1"
    with db_conn() as conn:
        return conn.execute(
            f"SELECT * FROM personnel_violations WHERE {sql} ORDER BY created_at DESC",
            params
        ).fetchall()

# =========================
# DB OPS — Site
# =========================
def insert_site_issue(row: dict):
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO site_issues (
                created_at, date_of_event, company,
                building, floor, risk_level, issue, photo_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row["created_at"], row["date_of_event"], row["company"],
            row["building"], row["floor"], row["risk_level"],
            row["issue"], row.get("photo_path")
        ))
        conn.commit()

def fetch_buildings():
    with db_conn() as conn:
        rows = conn.execute("SELECT DISTINCT building FROM site_issues ORDER BY building").fetchall()
    return [r["building"] for r in rows]

def fetch_site(filters: dict):
    where, params = [], []
    if filters.get("building") and filters["building"] != "(All)":
        where.append("building = ?")
        params.append(filters["building"])
    if filters.get("floor") and filters["floor"] != "(All)":
        where.append("floor = ?")
        params.append(filters["floor"])
    if filters.get("risk_level") and filters["risk_level"] != "(All)":
        where.append("risk_level = ?")
        params.append(filters["risk_level"])
    if filters.get("company_contains"):
        where.append("company LIKE ?")
        params.append(f"%{filters['company_contains']}%")
    if filters.get("keyword"):
        where.append("issue LIKE ?")
        params.append(f"%{filters['keyword']}%")

    sql = " AND ".join(where) if where else "1=1"
    with db_conn() as conn:
        return conn.execute(
            f"SELECT * FROM site_issues WHERE {sql} ORDER BY created_at DESC",
            params
        ).fetchall()

# =========================
# MAIN APP
# =========================
ensure_logged_in()
init_db_and_migrate()

st.set_page_config(page_title=APP_TITLE, layout="wide")

top = st.columns([4, 1])
with top[0]:
    st.title(APP_TITLE)
with top[1]:
    if st.button("Logout"):
        st.session_state.logged_in = False
        st.rerun()

st.caption("Personnel violations save evidence under: violation_data/people/<hardhat>/evidence/  |  Site issues save evidence under: violation_data/site_issues/evidence/")

mode = st.radio(
    "Select Entry Type",
    ["Personnel Safety Violation (Hard Hat #)", "Site Safety Issue (Building/Floor)"],
    horizontal=True
)

st.divider()

if mode == "Personnel Safety Violation (Hard Hat #)":
    tab_log, tab_review = st.tabs(["Log Personnel Violation", "Review / Search"])

    with tab_log:
        st.subheader("Personnel Safety Violation")

        col1, col2, col3 = st.columns(3)
        with col1:
            hh_raw = st.text_input("Hard Hat Number *", placeholder="Example: 117")
            company = st.text_input("Company (optional)")
            trade = st.text_input("Trade (optional)")
        with col2:
            date_event = st.date_input("Date of Event *")
            location = st.text_input("Location / Area (optional)")
            v_type = st.selectbox("Violation Type *", [
                "PPE", "Fall Protection", "Lift / AWP", "Scaffold",
                "Housekeeping", "Electrical", "Hot Work",
                "Rigging", "LOTO", "Excavation/Trenching", "Traffic Control",
                "Tools/Equipment", "Other"
            ])
        with col3:
            severity = st.selectbox("Severity *", ["Low", "Medium", "High", "Critical"])
            evidence = st.file_uploader("Upload Evidence (optional)", key="person_evidence")

        description = st.text_area("What happened? *", placeholder="Clear, objective description.")
        corrective_action = st.text_area("Corrective Action / Coaching (optional)")

        hh = clean_token(hh_raw)

        if hh:
            total, last30 = count_personnel_violations(hh)
            st.caption(f"History for Hard Hat #{hh} → Total: {total} | Last 30 days: {last30}")
            if total >= REPEAT_THRESHOLD_TOTAL or last30 >= REPEAT_THRESHOLD_30D:
                st.warning("⚠️ Repeat offender threshold hit — consider escalation.")

        if st.button("Save Personnel Violation"):
            if not hh:
                st.error("Hard Hat Number is required.")
            elif not description.strip():
                st.error("Description is required.")
            else:
                evidence_path = save_evidence_personnel(hh, evidence)
                insert_personnel_violation({
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
                    "evidence_path": evidence_path
                })
                st.success(f"Saved personnel violation for Hard Hat #{hh}")
                if evidence_path:
                    st.code(evidence_path)

    with tab_review:
        st.subheader("Review / Search — Personnel Violations")

        col1, col2, col3, col4 = st.columns(4)
        hardhats = ["(All)"] + fetch_hardhats()

        with col1:
            hh_pick = st.selectbox("Hard Hat #", hardhats)
        with col2:
            type_pick = st.selectbox("Type", ["(All)", "PPE", "Fall Protection", "Lift / AWP", "Scaffold",
                                              "Housekeeping", "Electrical", "Hot Work", "Rigging", "LOTO",
                                              "Excavation/Trenching", "Traffic Control", "Tools/Equipment", "Other"])
        with col3:
            sev_pick = st.selectbox("Severity", ["(All)", "Low", "Medium", "High", "Critical"])
        with col4:
            keyword = st.text_input("Keyword", placeholder="search description/location/company/trade")

        rows = fetch_personnel({
            "hard_hat": hh_pick,
            "type": type_pick,
            "severity": sev_pick,
            "keyword": keyword.strip() if keyword.strip() else None
        })

        st.write(f"Results: **{len(rows)}**")
        for r in rows:
            d = dict(r)
            title = f"{d.get('date_of_event','')} | HH#{d.get('hard_hat_number','')} | {d.get('violation_type','')} | {d.get('severity','')}"
            with st.expander(title):
                st.write(f"**Location:** {d.get('location') or '—'}")
                st.write(f"**Company / Trade:** {d.get('company') or '—'} / {d.get('trade') or '—'}")
                st.write(f"**Description:** {d.get('description')}")
                st.write(f"**Corrective Action:** {d.get('corrective_action') or '—'}")
                if d.get("evidence_path"):
                    st.code(d.get("evidence_path"))

else:
    tab_log, tab_review = st.tabs(["Log Site Issue", "Review / Search"])

    with tab_log:
        st.subheader("Site Safety Issue")

        col1, col2, col3 = st.columns(3)
        with col1:
            company = st.text_input("Company Responsible *", placeholder="Example: ABC Electric")
            building = st.text_input("Building *", placeholder="Example: West Addition / Building A")
        with col2:
            floor = st.text_input("Floor *", placeholder="Example: 1, 2, Mezz, Roof")
            date_event = st.date_input("Date Observed *")
        with col3:
            risk_level = st.selectbox("Risk Level *", ["Low", "Medium", "High", "Critical"])
            photo = st.file_uploader("Upload Photo (optional)", key="site_photo")

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
                photo_path = save_evidence_site(photo)
                insert_site_issue({
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "date_of_event": date_event.isoformat(),
                    "company": company.strip(),
                    "building": building.strip(),
                    "floor": floor.strip(),
                    "risk_level": risk_level,
                    "issue": issue.strip(),
                    "photo_path": photo_path
                })
                st.success("Saved site safety issue")
                if photo_path:
                    st.code(photo_path)

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

        rows = fetch_site({
            "building": building_pick,
            "floor": floor_pick,
            "risk_level": risk_pick,
            "company_contains": company_contains.strip() if company_contains.strip() else None,
            "keyword": keyword.strip() if keyword.strip() else None
        })

        st.write(f"Results: **{len(rows)}**")
        for r in rows:
            d = dict(r)
            title = f"{d.get('date_of_event','')} | {d.get('company','—')} | {d.get('building','—')} | Floor {d.get('floor','—')} | Risk {d.get('risk_level','—')}"
            with st.expander(title):
                st.write(f"**Issue:** {d.get('issue')}")
                if d.get("photo_path"):
                    st.code(d.get("photo_path"))
