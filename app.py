import re
import uuid
from datetime import datetime, date, timedelta

import streamlit as st
from supabase import create_client
from fpdf import FPDF

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
# SUPABASE
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
    t = (text or "").strip().replace(" ", "_")
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", t) or "unknown"

def iso(d: date) -> str:
    return d.isoformat()

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

def upload_to_storage(file, folder: str) -> str | None:
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
    return res.get("signedURL") if isinstance(res, dict) else None


def show_table_setup_help_if_needed():
    st.info(
        "If this is your first time using the database, you must create two tables in Supabase once.\n\n"
        "Supabase → SQL Editor → New query → Run the SQL shown below."
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
# DATA QUERIES
# -------------------------
def fetch_personnel(start_date: date, end_date: date):
    res = (
        supabase.table("personnel_violations")
        .select("*")
        .gte("date_event", iso(start_date))
        .lte("date_event", iso(end_date))
        .order("date_event", desc=True)
        .execute()
    )
    return res.data or []

def fetch_site(start_date: date, end_date: date):
    res = (
        supabase.table("site_issues")
        .select("*")
        .gte("date_event", iso(start_date))
        .lte("date_event", iso(end_date))
        .order("date_event", desc=True)
        .execute()
    )
    return res.data or []


# -------------------------
# PDF GENERATOR (FIXED WRAPPING)
# -------------------------
class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "Lilly Safety Hub - Safety Report", ln=True)
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "", 9)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def add_soft_breaks(s: str) -> str:
    """
    Insert spaces after separators so fpdf2 can wrap long tokens
    (paths, UUIDs, URLs, etc.) and not crash.
    """
    if s is None:
        return ""
    s = str(s)
    # Add "break opportunities" after common separators
    for ch in ["/", "_", "-", ".", ":", "?", "&", "="]:
        s = s.replace(ch, ch + " ")
    return s

def pdf_safe(text):
    # Make text latin-1 safe AND add wrap points
    t = add_soft_breaks("" if text is None else str(text))
    return t.encode("latin-1", "replace").decode("latin-1")

def add_section_title(pdf, title):
    pdf.set_font("Helvetica", "B", 12)
    pdf.multi_cell(0, 7, pdf_safe(title))
    pdf.ln(1)

def add_kv(pdf, k, v):
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(40, 6, pdf_safe(k))
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, pdf_safe(v))

def build_pdf(report_title: str, start_date: date, end_date: date, personnel_rows, site_rows) -> bytes:
    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, pdf_safe(report_title))
    pdf.ln(2)

    add_kv(pdf, "Date range:", f"{iso(start_date)} to {iso(end_date)}")
    add_kv(pdf, "Generated:", now_iso())
    pdf.ln(3)

    add_section_title(pdf, "Summary")
    add_kv(pdf, "Personnel violations:", str(len(personnel_rows)))
    add_kv(pdf, "Site safety issues:", str(len(site_rows)))
    pdf.ln(2)

    add_section_title(pdf, "Personnel Safety Violations")
    if not personnel_rows:
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 6, "None recorded in this range.")
    else:
        for r in personnel_rows:
            pdf.set_font("Helvetica", "B", 10)
            title = f"{r.get('date_event','')} | HH#{r.get('hard_hat','')} | {r.get('violation_type','')} | {r.get('severity','')}"
            pdf.multi_cell(0, 6, pdf_safe(title))
            pdf.set_font("Helvetica", "", 10)

            if r.get("company"): add_kv(pdf, "Company:", r.get("company"))
            if r.get("trade"): add_kv(pdf, "Trade:", r.get("trade"))
            if r.get("location"): add_kv(pdf, "Location:", r.get("location"))

            add_kv(pdf, "What happened:", r.get("description") or "")
            if r.get("corrective"): add_kv(pdf, "Corrective action:", r.get("corrective"))
            if r.get("evidence_path"): add_kv(pdf, "Evidence path:", r.get("evidence_path"))

            pdf.ln(2)

    pdf.ln(1)

    add_section_title(pdf, "Site Safety Issues")
    if not site_rows:
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 6, "None recorded in this range.")
    else:
        for r in site_rows:
            pdf.set_font("Helvetica", "B", 10)
            title = f"{r.get('date_event','')} | {r.get('company','')} | {r.get('building','')} | Floor {r.get('floor','')} | {r.get('risk_level','')}"
            pdf.multi_cell(0, 6, pdf_safe(title))
            pdf.set_font("Helvetica", "", 10)

            add_kv(pdf, "Issue:", r.get("issue") or "")
            if r.get("photo_path"): add_kv(pdf, "Photo path:", r.get("photo_path"))

            pdf.ln(2)

    return pdf.output(dest="S").encode("latin-1")


# -------------------------
# APP START
# -------------------------
require_login()

st.title(APP_TITLE)
st.success("Permanent storage: ON (Supabase DB via REST + Supabase Storage)")

show_table_setup_help_if_needed()

tabs = st.tabs(["Log Entries", "Reports"])

# =========================
# TAB 1 — LOG ENTRIES
# =========================
with tabs[0]:
    mode = st.radio(
        "Select Entry Type",
        ["Personnel Safety Violation (Hard Hat #)", "Site Safety Issue (Building/Floor)"],
        horizontal=True,
        key="entry_mode"
    )

    st.divider()

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
                    st.error("Database insert failed (tables may not exist yet).")
                    st.code(str(e))

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
                    st.error("Database insert failed (tables may not exist yet).")
                    st.code(str(e))


# =========================
# TAB 2 — REPORTS (PDF)
# =========================
with tabs[1]:
    st.subheader("PDF Reports")

    report_type = st.selectbox("Report Type", ["Daily", "Weekly", "Custom Range"])

    today = date.today()
    if report_type == "Daily":
        start_date = st.date_input("Report Date", value=today)
        end_date = start_date
        title = f"Daily Safety Report - {iso(start_date)}"
    elif report_type == "Weekly":
        default_start = today - timedelta(days=6)
        start_date = st.date_input("Start Date", value=default_start)
        end_date = st.date_input("End Date", value=today)
        title = f"Weekly Safety Report - {iso(start_date)} to {iso(end_date)}"
    else:
        start_date = st.date_input("Start Date", value=today - timedelta(days=7))
        end_date = st.date_input("End Date", value=today)
        title = f"Safety Report - {iso(start_date)} to {iso(end_date)}"

    st.caption("Generates a downloadable PDF from Supabase records.")

    if st.button("Generate PDF"):
        try:
            personnel_rows = fetch_personnel(start_date, end_date)
            site_rows = fetch_site(start_date, end_date)

            pdf_bytes = build_pdf(title, start_date, end_date, personnel_rows, site_rows)

            filename = f"lilly_safety_report_{iso(start_date)}_to_{iso(end_date)}.pdf"
            st.success("PDF created.")
            st.download_button(
                "Download PDF",
                data=pdf_bytes,
                file_name=filename,
                mime="application/pdf",
            )

        except Exception as e:
            st.error("Failed to generate report.")
            st.code(str(e))


st.divider()
if st.button("Logout"):
    st.session_state.auth = False
    st.rerun()
