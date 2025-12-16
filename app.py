import re
import uuid
from datetime import datetime, date

import streamlit as st
from supabase import create_client

APP_TITLE = "Lilly Safety Hub"

st.set_page_config(page_title=APP_TITLE, layout="wide")

# -------------------------
# AUTH
# -------------------------
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


# -------------------------
# SUPABASE CLIENT
# -------------------------
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = st.secrets["SUPABASE_SERVICE_KEY"]
SUPABASE_BUCKET = st.secrets.get("SUPABASE_BUCKET", "evidence")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# -------------------------
# HELPERS
# -------------------------
def clean_token(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "", (text or "").strip())

def safe_folder(text: str) -> str:
    # good for building/floor folder names
    t = (text or "").strip().replace(" ", "_")
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", t) or "unknown"

def upload_to_storage(file, folder: str) -> str | None:
    """
    Uploads to Supabase Storage bucket and returns the storage path.
    """
    if not file:
        return None

    ext = file.name.split(".")[-1] if "." in file.name else "bin"
    path = f"{folder}/{uuid.uuid4().hex}.{ext}"

    supabase.storage.from_(SUPABASE_BUCKET).upload(
        path,
        file.getvalue(),
        file_options={"content-type": file.type or "application/octet-stream", "upsert": "true"},
    )
    return path

def signed_url(path: str, seconds: int = 3600) -> str | None:
    if not path:
        return None
    res = supabase.storage.from_(SUPABASE_BUCKET).create_signed_url(path, seconds)
    # supabase-py returns dict-like
    return res.get("signedURL") if isinstance(res, dict) else None

def iso(d: date) -> str:
    return d.isoformat()

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# -------------------------
# ONE-TIME TABLE CHECK MESSAGE
# -------------------------
def show_table_setup_help_if_needed():
    # If tables don't exist, inserts will fail. We show the SQL the user must run once.
    st.info(
        "If this is your first time using the database, you must create two tables in Supabase once.\n\n"
        "Supabase → SQL Editor → New query → Run the SQL shown in the 'Create tables (one-time)' section below."
    )
    with st.expander("Create tables (one-time) — click to open"):
        st.code(
            """
-- Run this ONCE in Supabase SQL Editor

create table if not exists public.personnel_violations (
  id bigserial primary key,
  created_at text,
  date_event text,
  hard_hat text,
  company text,
  trade text,
  location text,
  violation_type text,
  severity text,
  description text,
  corrective text,
  evidence_path text
);

create table if not exists public.site_issues (
  id bigserial primary key,
  created_at text,
  date_event text,
  company text,
  building text,
  floor text,
  risk_level text,
  issue text,
  photo_path text
);
            """.strip(),
            language="sql"
        )


# -------------------------
# APP START
# -------------------------
require_login()

st.title(APP_TITLE)
st.success("Permanent storage: ON (Supabase DB via REST + Supabase Storage)")

show_table_setup_help_if_needed()

mode = st.radio(
    "Select Entry Type",
    ["Personnel Safety Violation (Hard Hat #)", "Site Safety Issue (Building/Floor)"],
    horizontal=True,
)

st.divider()

# -------------------------
# PERSONNEL VIOLATIONS
# -------------------------
if mode == "Personnel Safety Violation (Hard Hat #)":
    st.subheader("Personnel Safety Violation")

    c1, c2, c3 = st.columns(3)
    with c1:
        hard_hat_raw = st.text_input("Hard Hat Number *", placeholder="Example: 117")
        company = st.text_input("Company (optional)")
        trade = st.text_input("Trade (optional)")
    with c2:
        date_event = st.date_input("Date of Event *")
        location = st.text_input("Location / Area (optional)")
        vtype = st.selectbox(
            "Violation Type *",
            ["PPE", "Fall Protection", "Lift / AWP", "Scaffold", "Housekeeping", "Electrical", "Hot Work", "Other"],
        )
    with c3:
        severity = st.selectbox("Severity *", ["Low", "Medium", "High", "Critical"])
        evidence = st.file_uploader("Upload Evidence (optional)", key="person_evidence")

    description = st.text_area("What happened? *")
    corrective = st.text_area("Corrective Action / Coaching (optional)")

    hard_hat = clean_token(hard_hat_raw)

    if st.button("Save Personnel Violation"):
        if not hard_hat:
            st.error("Hard Hat Number is required.")
        elif not description.strip():
            st.error("Description is required.")
        else:
            evidence_path = upload_to_storage(evidence, f"people/{hard_hat}") if evidence else None

            payload = {
                "created_at": now_iso(),
                "date_event": iso(date_event),
                "hard_hat": hard_hat,
                "company": company.strip() or None,
                "trade": trade.strip() or None,
                "location": location.strip() or None,
                "violation_type": vtype,
                "severity": severity,
                "description": description.strip(),
                "corrective": corrective.strip() or None,
                "evidence_path": evidence_path,
            }

            try:
                supabase.table("personnel_violations").insert(payload).execute()
                st.success(f"Saved personnel violation for HH#{hard_hat}")

                if evidence_path:
                    url = signed_url(evidence_path)
                    if url:
                        st.link_button("Open evidence (signed link)", url)
                    st.caption(f"Stored at: {evidence_path}")

            except Exception as e:
                st.error("Database insert failed. Most likely the table does not exist yet.")
                st.code(str(e))


# -------------------------
# SITE SAFETY ISSUES
# -------------------------
else:
    st.subheader("Site Safety Issue")

    c1, c2, c3 = st.columns(3)
    with c1:
        company = st.text_input("Company Responsible *", placeholder="Example: ABC Electric")
        building = st.text_input("Building *", placeholder="Example: West Addition")
    with c2:
        floor = st.text_input("Floor *", placeholder="Example: 1, 2, Roof")
        date_event = st.date_input("Date Observed *")
    with c3:
        risk = st.selectbox("Risk Level *", ["Low", "Medium", "High", "Critical"])
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
            b = safe_folder(building)
            f = safe_folder(floor)

            photo_path = upload_to_storage(photo, f"site/{b}/floor_{f}") if photo else None

            payload = {
                "created_at": now_iso(),
                "date_event": iso(date_event),
                "company": company.strip(),
                "building": building.strip(),
                "floor": floor.strip(),
                "risk_level": risk,
                "issue": issue.strip(),
                "photo_path": photo_path,
            }

            try:
                supabase.table("site_issues").insert(payload).execute()
                st.success("Saved site safety issue")

                if photo_path:
                    url = signed_url(photo_path)
                    if url:
                        st.link_button("Open photo (signed link)", url)
                    st.caption(f"Stored at: {photo_path}")

            except Exception as e:
                st.error("Database insert failed. Most likely the table does not exist yet.")
                st.code(str(e))


st.divider()
if st.button("Logout"):
    st.session_state.auth = False
    st.rerun()
