# Peninsular Quote Processor

A local Streamlit app to process Outlook `.msg` quote emails without any paid API.

It extracts quote PDFs from `.msg` files, renames them, and creates an Excel/CSV output matching the quote account workflow.

## What it does

- Upload multiple `.msg` files.
- Extract quote number from subject, e.g. `QT# 622900`.
- Extract received/sent date from email.
- Extract PDF attachments.
- Rename PDFs as `Peninsular_<QuoteNumber>.pdf` by default.
- Create Excel and CSV output with:
  - ReferralName
  - ReferralEmail
  - QuoteNumber
  - QuoteDate
  - PDF
  - SourceMSG
  - Status
  - Notes

## Important limitation

This version is rule-based and does **not** use any AI/API. It is best for:

- Quote number
- Quote date
- PDF extraction/renaming
- Basic customer name/email detection from email thread

Messy forwarded email trails may still need review. Rows with lower confidence are marked in `Status` and `Notes`.

## Install locally

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Mac/Linux
pip install -r requirements.txt
streamlit run app.py
```

## GitHub upload

1. Create a new GitHub repo.
2. Upload all files from this folder.
3. Run with Streamlit locally or deploy to Streamlit Community Cloud.

## Folder outputs

When you process files, the app creates a ZIP containing:

```text
output/
  renamed_pdfs/
    Peninsular_622900.pdf
  Peninsular_quote_output.xlsx
  Peninsular_quote_output.csv
```

## Recommended naming convention

Default:

```text
Peninsular_<QuoteNumber>.pdf
```

Example:

```text
Peninsular_622900.pdf
```
