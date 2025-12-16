import re
import uuid
from datetime import datetime, date, timedelta
from io import BytesIO

import streamlit as st
from supabase import create_client

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.units import inch


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
# PDF (ReportLab) — robust wrapping
# -------------------------
def para(text: str) -> str:
    """Escape minimal HTML for ReportLab Paragraph."""
    if text is None:
        return ""
    s = str(text)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Add zero-width break opportunities after separators to help wrap long paths/IDs
    for ch in ["/", "_", "-", ".", ":", "?", "&", "=", "@"]:
        s = s.replace(ch, ch + "&#8203;")  # zero-width space
    return s

def build_pdf_report(report_title: str, start_date: date, end_date: date, personnel_rows, site_rows) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title="Lilly Safety Hub Report",
    )

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    h_style = styles["Heading2"]
    subh_style = styles["Heading3"]
    body = styles["BodyText"]

    body_wrap = ParagraphStyle(
        "BodyWrap",
        parent=body,
        leading=13,
        wordWrap="CJK",   # allows splitting long “words”
    )

    story = []

    story.append(Paragraph(para("Lilly Safety Hub - Safety Report"), title_style))
    story.append(Spacer(1, 8))
    story.append(Paragraph(para(report_title), body_wrap))
    story.append(Spacer(1, 10))

    story.append(Paragraph(para(f"<b>Date range:</b> {iso(start_date)} to {iso(end_date)}"), body_wrap))
    story.append(Paragraph(para(f"<b>Generated:</b> {now_iso()}"), body_wrap))
    story.append(Spacer(1, 12))

    story.append(Paragraph(para("Summary"), h_style))
    story.append(Paragraph(para(f"<b>Personnel violations:</b> {len(personnel_rows)}"), body_wrap))
    story.append(Paragraph(para(f"<b>Site safety issues:</b> {len(site_rows)}"), body_wrap))
    story.append(Spacer(1, 12))

    # Personnel section
    story.append(Paragraph(para("Personnel Safety Violations"), h_style))
    story.append(Spacer(1, 6))
    if not personnel_rows:
        story.append(Paragraph(para("None recorded in this range."), body_wrap))
        story.append(Spacer(1, 10))
    else:
        for r in personnel_rows:
            header = f"{r.get('date_event','')} | HH#{r.get('hard_hat','')} | {r.get('violation_type','')} | {r.get('severity','')}"
            story.append(Paragraph(para(f"<b>{header}</b>"), body_wrap))

            if r.get("company"):
                story.append(Paragraph(para(f"<b>Company:</b> {r.get('company')}"), body_wrap))
            if r.get("trade"):
                story.append(Paragraph(para(f"<b>Trade:</b> {r.get('trade')}"), body_wrap))
            if r.get("location"):
                story.append(Paragraph(para(f"<b>Location:</b> {r.get('location')}"), body_wrap))

            story.append(Paragraph(para(f"<b>What happened:</b> {r.get('description') or ''}"), body_wrap))

            if r.get("corrective"):
                story.append(Paragraph(para(f"<b>Corrective action:</b> {r.get('corrective')}"), body_wrap))

            if r.get("evidence_path"):
                story.append(Paragraph(para(f"<b>Evidence path:</b> {r.get('evidence_path')}"), body_wrap))

            story.append(Spacer(1, 10))

    story.append(PageBreak())

    # Site section
    story.append(Paragraph(para("Site Safety Issues"), h_style))
    story.append(Spacer(1, 6))
    if not site_rows:
        story.append(Paragraph(para("None recorded in this range."), body_wrap))
        story.append(Spacer(1, 10))
    else:
        for r in site_rows:
            header = f"{r.get('date_event','')} | {r.get('company','')} | {r.get('building','')} | Floor {r.get('floor','')} | {r.get('risk_level','')}"
            story.append(Paragraph(para(f"<b>{header}</b>"), body_wrap))

            story.append(Paragraph(para(f"<b>Issue:</b> {r.get('issue') or ''}"), body_wrap))

            if r.get("photo_path"):
                story.append(Paragraph(para(f"<b>Photo path:</b> {r.get('photo_path')}"), body_wrap))

            story.append(Spacer(1, 10))

    doc.build(story)
    return buf.getvalue()


# -------------------------
# APP START
# -------------------------
require_login()

st.title(APP_TITLE)
st.success("Permanent storage: ON (Supabase DB via REST + Supabase Storage)")
show_table_setup_help_if_needed()

tabs = st.tabs(["Log Entries", "Reports"])

# -------------------------
# TAB 1: LOG ENTRIES
# -------------------------
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


# -------------------------
# TAB 2: REPORTS (PDF)
# -------------------------
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

            pdf_bytes = build_pdf_report(title, start_date, end_date, personnel_rows, site_rows)

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
