# =========================
# Imports
# =========================
import re
import uuid
from datetime import datetime

import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor
from supabase import create_client


# =========================
# App Config
# =========================
APP_TITLE = "Lilly Safety Hub"
st.set_page_config(page_title=APP_TITLE, layout="wide")


# =========================
# Authentication
# =========================
def require_login():
    if "auth" not in st.session_state:
        st.session_state.auth = False

    if st.session_state.auth:
        return

    st.title(APP_TITLE)
    pw = st.text_input("Password", type="password")

    if st.button("Login"):
        if pw == st.secrets["APP_PASSWORD"]:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("Invalid password")

    st.stop()


# =========================
# Secrets
# =========================
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_SERVICE_KEY"]
SUPABASE_BUCKET = st.secrets["SUPABASE_BUCKET"]
DATABASE_URL = st.secrets["DATABASE_URL"]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# =========================
# Database
# =========================
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS personnel_violations (
            id SERIAL PRIMARY KEY,
            created_at TEXT,
            date_event TEXT,
            hard_hat TEXT,
            company TEXT,
            trade TEXT,
            location TEXT,
            violation_type TEXT,
            severity TEXT,
            description TEXT,
            corrective TEXT,
            evidence_path TEXT
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS site_issues (
            id SERIAL PRIMARY KEY,
            created_at TEXT,
            date_event TEXT,
            company TEXT,
            building TEXT,
            floor TEXT,
            risk_level TEXT,
            issue TEXT,
            photo_path TEXT
        );
        """)

        conn.commit()


# =========================
# Helpers
# =========================
def clean(text):
    return re.sub(r"[^a-zA-Z0-9_-]", "", text.strip())


def upload_to_supabase(file, folder):
    if not file:
        return None

    ext = file.name.split(".")[-1]
    name = f"{folder}/{uuid.uuid4()}.{ext}"

    supabase.storage.from_(SUPABASE_BUCKET).upload(
        name,
        file.getvalue(),
        file_options={"content-type": file.type}
    )
    return name


def signed_url(path):
    if not path:
        return None
    return supabase.storage.from_(SUPABASE_BUCKET).create_signed_url(path, 3600)["signedURL"]


# =========================
# App Start
# =========================
require_login()
init_db()

st.title(APP_TITLE)
st.caption("Secure safety reporting powered by Supabase")

mode = st.radio(
    "Select Entry Type",
    ["Personnel Safety Violation", "Site Safety Issue"],
    horizontal=True
)

st.divider()

# =========================
# Personnel Violations
# =========================
if mode == "Personnel Safety Violation":
    st.subheader("Personnel Safety Violation")

    c1, c2, c3 = st.columns(3)

    with c1:
        hard_hat = clean(st.text_input("Hard Hat Number *"))
        company = st.text_input("Company")
        trade = st.text_input("Trade")

    with c2:
        date_event = st.date_input("Date of Event *")
        location = st.text_input("Location / Area")
        vtype = st.selectbox("Violation Type", ["PPE", "Fall Protection", "Lift", "Electrical", "Other"])

    with c3:
        severity = st.selectbox("Severity", ["Low", "Medium", "High", "Critical"])
        evidence = st.file_uploader("Upload Evidence")

    description = st.text_area("What happened? *")
    corrective = st.text_area("Corrective Action")

    if st.button("Save Personnel Violation"):
        if not hard_hat or not description:
            st.error("Hard hat number and description required")
        else:
            path = upload_to_supabase(evidence, f"people/{hard_hat}")

            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("""
                INSERT INTO personnel_violations
                VALUES (DEFAULT,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    datetime.now().isoformat(),
                    date_event.isoformat(),
                    hard_hat,
                    company,
                    trade,
                    location,
                    vtype,
                    severity,
                    description,
                    corrective,
                    path
                ))
                conn.commit()

            st.success("Personnel violation saved")
            if path:
                st.link_button("View Evidence", signed_url(path))


# =========================
# Site Safety Issues
# =========================
else:
    st.subheader("Site Safety Issue")

    c1, c2, c3 = st.columns(3)

    with c1:
        company = st.text_input("Company Responsible *")
        building = st.text_input("Building *")

    with c2:
        floor = st.text_input("Floor *")
        date_event = st.date_input("Date Observed *")

    with c3:
        risk = st.selectbox("Risk Level", ["Low", "Medium", "High", "Critical"])
        photo = st.file_uploader("Upload Photo")

    issue = st.text_area("Describe the Issue *")

    if st.button("Save Site Safety Issue"):
        if not company or not building or not floor or not issue:
            st.error("All required fields must be completed")
        else:
            folder = f"site/{building.replace(' ', '_')}/floor_{floor}"
            path = upload_to_supabase(photo, folder)

            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("""
                INSERT INTO site_issues
                VALUES (DEFAULT,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    datetime.now().isoformat(),
                    date_event.isoformat(),
                    company,
                    building,
                    floor,
                    risk,
                    issue,
                    path
                ))
                conn.commit()

            st.success("Site safety issue saved")
            if path:
                st.link_button("View Photo", signed_url(path))


st.divider()
if st.button("Logout"):
    st.session_state.auth = False
    st.rerun()
