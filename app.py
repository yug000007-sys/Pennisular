import io
import os
import re
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable, Optional

import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

try:
    import extract_msg
except Exception:  # pragma: no cover
    extract_msg = None


APP_TITLE = "Peninsular Quote Processor"
DEFAULT_PREFIX = "Peninsular"
OUTPUT_COLUMNS = [
    "ReferralName",
    "ReferralEmail",
    "QuoteNumber",
    "QuoteDate",
    "PDF",
    "SourceMSG",
    "Status",
    "Notes",
]

SUPPLIER_DOMAINS = {
    "peninsularcylinders.com",
    "peninsular-cylinder.com",
}

GENERIC_MAILBOX_WORDS = {
    "sales",
    "quote",
    "quotes",
    "rfq",
    "info",
    "orders",
    "purchasing",
    "service",
    "support",
}


@dataclass
class QuoteRow:
    ReferralName: str = ""
    ReferralEmail: str = ""
    QuoteNumber: str = ""
    QuoteDate: str = ""
    PDF: str = ""
    SourceMSG: str = ""
    Status: str = "Needs Review"
    Notes: str = ""


def clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    value = re.sub(r"\r\n|\r", "\n", str(value))
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def html_to_text(html: Optional[str]) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    return clean_text(soup.get_text("\n"))


def safe_filename(name: str) -> str:
    name = re.sub(r"[<>:\\|?*\x00-\x1f]", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180]


def extract_quote_number(subject: str, filename: str) -> str:
    haystack = f"{subject or ''} {filename or ''}"
    patterns = [
        r"\bQT\s*#?\s*(\d{5,7})\b",
        r"\bQuote\s*#?\s*(\d{5,7})\b",
        r"\bQ(?:uote)?[-_\s#]*(\d{5,7})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, haystack, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def normalize_date(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    try:
        return date_parser.parse(str(value), fuzzy=True).date().isoformat()
    except Exception:
        return ""


def parse_sender_email(sender: str) -> str:
    if not sender:
        return ""
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", sender, flags=re.I)
    return match.group(0).lower() if match else ""


def email_domain(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def is_supplier_email(email: str) -> bool:
    return email_domain(email) in SUPPLIER_DOMAINS


def name_from_email(email: str) -> str:
    if not email or "@" not in email:
        return ""
    local = email.split("@")[0]
    parts = re.split(r"[._\-]+", local)
    parts = [p for p in parts if p and p.lower() not in GENERIC_MAILBOX_WORDS and not p.isdigit()]
    if not parts:
        return ""
    return " ".join(p.capitalize() for p in parts)


def find_external_contacts(text: str) -> list[tuple[str, str]]:
    """Return likely external contacts as (name, email), preserving thread order."""
    contacts: list[tuple[str, str]] = []
    if not text:
        return contacts

    # Common Outlook patterns: From: Name <email>, Name [mailto:email], or plain email.
    from_lines = re.findall(r"(?im)^\s*From:\s*(.+)$", text)
    candidates = from_lines + re.findall(r"[^\n<>]*[<\[]?([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})[>\]]?", text, flags=re.I)

    seen = set()
    for raw in candidates:
        email_match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", raw, flags=re.I)
        if not email_match:
            continue
        email = email_match.group(0).lower()
        if email in seen or is_supplier_email(email):
            continue
        seen.add(email)
        before_email = raw[: email_match.start()].strip(" <>[]'\"")
        before_email = re.sub(r"(?i)^from:\s*", "", before_email).strip()
        before_email = re.sub(r"(?i)mailto:\s*", "", before_email).strip()
        before_email = re.sub(r"\s+", " ", before_email)
        name = before_email if before_email and "@" not in before_email else name_from_email(email)
        contacts.append((name, email))
    return contacts


def choose_referral_contact(sender: str, body_text: str) -> tuple[str, str, str]:
    sender_email = parse_sender_email(sender)
    if sender_email and not is_supplier_email(sender_email):
        return name_from_email(sender_email), sender_email, "Used top-level sender because sender is external."

    contacts = find_external_contacts(body_text)
    if contacts:
        name, email = contacts[0]
        return name, email, "Used first external contact found in email thread."

    if sender_email:
        return name_from_email(sender_email), sender_email, "Only supplier or ambiguous sender found; review needed."
    return "", "", "No external customer email found."


def attachment_filename(att) -> str:
    for attr in ("longFilename", "shortFilename", "filename"):
        value = getattr(att, attr, None)
        if value:
            return str(value)
    return "attachment"


def save_attachment(att, target_path: Path) -> None:
    data = getattr(att, "data", None)
    if data:
        target_path.write_bytes(data)
        return
    # Fallback for extract_msg attachment API.
    with TemporaryDirectory() as td:
        att.save(customPath=td)
        saved = list(Path(td).glob("*"))
        if not saved:
            raise RuntimeError("Attachment could not be saved.")
        target_path.write_bytes(saved[0].read_bytes())


def process_msg_file(uploaded_file, output_dir: Path, prefix: str) -> QuoteRow:
    if extract_msg is None:
        raise RuntimeError("extract-msg is not installed. Run: pip install -r requirements.txt")

    source_name = uploaded_file.name
    msg_path = output_dir / "source_msg" / safe_filename(source_name)
    msg_path.parent.mkdir(parents=True, exist_ok=True)
    msg_path.write_bytes(uploaded_file.getbuffer())

    row = QuoteRow(SourceMSG=source_name)
    msg = extract_msg.Message(str(msg_path))
    try:
        subject = clean_text(getattr(msg, "subject", ""))
        sent_date = getattr(msg, "date", None) or getattr(msg, "parsedDate", None)
        sender = clean_text(getattr(msg, "sender", ""))
        body = clean_text(getattr(msg, "body", ""))
        html_body = html_to_text(getattr(msg, "htmlBody", ""))
        body_text = clean_text("\n".join([body, html_body]))

        quote_number = extract_quote_number(subject, source_name)
        quote_date = normalize_date(sent_date)
        ref_name, ref_email, contact_note = choose_referral_contact(sender, body_text)

        row.ReferralName = ref_name
        row.ReferralEmail = ref_email
        row.QuoteNumber = quote_number
        row.QuoteDate = quote_date

        pdf_attachments = []
        for att in getattr(msg, "attachments", []) or []:
            original_name = attachment_filename(att)
            if original_name.lower().endswith(".pdf"):
                pdf_attachments.append((att, original_name))

        notes = []
        if contact_note:
            notes.append(contact_note)
        if not quote_number:
            notes.append("Quote number not found.")
        if not quote_date:
            notes.append("Quote date not found.")
        if not pdf_attachments:
            notes.append("No PDF attachment found.")

        if pdf_attachments and quote_number:
            pdf_dir = output_dir / "renamed_pdfs"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            base_name = f"{prefix}_{quote_number}.pdf"
            target_name = safe_filename(base_name)
            target_path = pdf_dir / target_name
            counter = 2
            while target_path.exists():
                target_name = safe_filename(f"{prefix}_{quote_number}_{counter}.pdf")
                target_path = pdf_dir / target_name
                counter += 1
            save_attachment(pdf_attachments[0][0], target_path)
            row.PDF = target_name
            if len(pdf_attachments) > 1:
                notes.append(f"Multiple PDFs found; used first PDF: {pdf_attachments[0][1]}")
        elif pdf_attachments:
            notes.append("PDF found but not renamed because quote number is missing.")

        required_ok = bool(row.QuoteNumber and row.QuoteDate and row.PDF)
        contact_ok = bool(row.ReferralEmail and not is_supplier_email(row.ReferralEmail))
        row.Status = "OK" if required_ok and contact_ok else "Needs Review"
        row.Notes = " ".join(notes).strip()
        return row
    finally:
        msg.close()


def build_outputs(rows: Iterable[QuoteRow], output_dir: Path) -> tuple[Path, Path, Path]:
    df = pd.DataFrame([asdict(r) for r in rows], columns=OUTPUT_COLUMNS)
    csv_path = output_dir / "Peninsular_quote_output.csv"
    xlsx_path = output_dir / "Peninsular_quote_output.xlsx"
    zip_path = output_dir.parent / "Peninsular_quote_output_package.zip"

    df.to_csv(csv_path, index=False)
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Quotes")
        ws = writer.book["Quotes"]
        ws.freeze_panes = "A2"
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 45)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in output_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(output_dir.parent))
    return csv_path, xlsx_path, zip_path


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="📄", layout="wide")
    st.title(APP_TITLE)
    st.write("Upload Outlook `.msg` quote emails. The app extracts PDF attachments, renames them, and creates Excel/CSV output.")

    with st.sidebar:
        st.header("Settings")
        prefix = st.text_input("PDF filename prefix", value=DEFAULT_PREFIX)
        st.caption("Default output example: Peninsular_622900.pdf")

    files = st.file_uploader("Upload .msg files", type=["msg"], accept_multiple_files=True)

    if not files:
        st.info("Upload one or more `.msg` files to start.")
        return

    if st.button("Process quote emails", type="primary"):
        with TemporaryDirectory() as td:
            work_dir = Path(td)
            output_dir = work_dir / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            rows = []
            errors = []
            progress = st.progress(0)
            for idx, file in enumerate(files, start=1):
                try:
                    rows.append(process_msg_file(file, output_dir, prefix.strip() or DEFAULT_PREFIX))
                except Exception as exc:
                    errors.append({"SourceMSG": file.name, "Error": str(exc)})
                progress.progress(idx / len(files))

            if rows:
                csv_path, xlsx_path, zip_path = build_outputs(rows, output_dir)
                df = pd.DataFrame([asdict(r) for r in rows], columns=OUTPUT_COLUMNS)
                st.subheader("Preview")
                st.dataframe(df, use_container_width=True)

                st.download_button(
                    "Download full output ZIP",
                    data=zip_path.read_bytes(),
                    file_name="Peninsular_quote_output_package.zip",
                    mime="application/zip",
                )
                st.download_button(
                    "Download Excel only",
                    data=xlsx_path.read_bytes(),
                    file_name="Peninsular_quote_output.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                st.download_button(
                    "Download CSV only",
                    data=csv_path.read_bytes(),
                    file_name="Peninsular_quote_output.csv",
                    mime="text/csv",
                )

            if errors:
                st.subheader("Errors")
                st.dataframe(pd.DataFrame(errors), use_container_width=True)


if __name__ == "__main__":
    main()
