import streamlit as st
import pandas as pd
import pdfplumber
import re
import io
from thefuzz import process

# -------------------------------------------------
# 1. CLEAN VENDOR DATA
# -------------------------------------------------
def clean_vendor_data(df):
    df = df.copy()
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.lower()
    )

    inv_col = next((c for c in df.columns if 'invoice' in c or 'inv' in c), None)
    amt_col = next((c for c in df.columns if 'amount' in c or 'due' in c), None)

    if not inv_col or not amt_col:
        raise ValueError("Vendor invoice/amount columns not found")

    df['clean_id'] = (
        df[inv_col].astype(str)
        .str.replace(r'\W+', '', regex=True)
        .str.upper()
        .str.lstrip('0')
    )

    df['clean_amount'] = (
        df[amt_col].astype(str)
        .str.replace(r'[,$\s]', '', regex=True)
    )

    df['clean_amount'] = pd.to_numeric(
        df['clean_amount'], errors='coerce'
    ).fillna(0).round(2)

    return df[['clean_id', 'clean_amount']]

# -------------------------------------------------
# 2. CLEAN INTERNAL DATA (NEGATIVE → POSITIVE)
# -------------------------------------------------
def clean_internal_data(df):
    df = df.copy()
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.lower()
    )

    inv_col = next((c for c in df.columns if 'external' in c and 'document' in c), None)
    amt_col = next((c for c in df.columns if 'amount' in c), None)

    if not inv_col or not amt_col:
        raise ValueError("Internal required columns missing")

    df['clean_id'] = (
        df[inv_col].astype(str)
        .str.replace(r'\W+', '', regex=True)
        .str.upper()
        .str.lstrip('0')
    )

    df['clean_amount'] = (
        df[amt_col].astype(str)
        .str.replace(r'[,$\s]', '', regex=True)
    )

    df['clean_amount'] = pd.to_numeric(
        df['clean_amount'], errors='coerce'
    ).fillna(0).abs().round(2)

    return df[['clean_id', 'clean_amount']]

# -------------------------------------------------
# 3. PDF EXTRACTION — FLEXIBLE + DEBUG
# -------------------------------------------------
def extract_vendor_pdf(file, debug=False):
    """
    Tries a strict FedEx-style regex first.
    Falls back to a lenient pattern that captures any line with:
      - An invoice-like token  (digits / hyphens, 6+ chars)
      - A currency amount      (digits with optional commas, 2 decimal places)
    Returns (dataframe, list_of_raw_lines) so the caller can surface debug info.
    """
    rows = []
    raw_lines = []

    # --- Pattern A: original strict FedEx format ---
    strict_pattern = re.compile(
        r'(9-\d{3,4}-\d{4,6})'          # invoice  e.g. 9-123-45678  (relaxed digit counts)
        r'\s+'
        r'(Freight|Duty[/\s&]+Tax)'      # charge type  (now accepts "Duty & Tax", "Duty / Tax")
        r'\s+'
        r'\d{1,2}\s+\w+\s+\d{2,4}'      # date e.g. 12 Jan 25
        r'\s+\d+'                         # shipment count
        r'\s+[A-Z]{3}'                    # any 3-letter currency (USD, HKD, SGD …)
        r'\s+([\d,]+\.\d{2})'            # gross amount
        r'\s+([\d,]+\.\d{2})',           # net / billed amount  ← captured
        re.IGNORECASE
    )

    # --- Pattern B: lenient fallback ---
    # Looks for a FedEx-style invoice number anywhere on the line,
    # then grabs the LAST currency amount on that line as the billed amount.
    lenient_inv    = re.compile(r'(9[-\s]\d{2,4}[-\s]\d{4,6})', re.IGNORECASE)
    lenient_amount = re.compile(r'([\d,]+\.\d{2})')

    with pdfplumber.open(file) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if not text:
                raw_lines.append(f"[Page {page_num}] <no text extracted — may be scanned/image PDF>")
                continue

            for line in text.split("\n"):
                raw_lines.append(f"[Page {page_num}] {line}")

                # Try strict match first
                m = strict_pattern.search(line)
                if m:
                    rows.append({
                        "invoice_no": m.group(1).strip(),
                        "amount":     m.group(5).replace(",", ""),
                        "match_type": "strict"
                    })
                    continue

                # Try lenient fallback
                inv_m = lenient_inv.search(line)
                if inv_m:
                    amounts = lenient_amount.findall(line)
                    if amounts:
                        rows.append({
                            "invoice_no": inv_m.group(1).strip(),
                            "amount":     amounts[-1].replace(",", ""),  # last amount on the line
                            "match_type": "lenient"
                        })

    if not rows:
        raise ValueError(
            "No valid invoices found in PDF.\n\n"
            "Expand '🔍 PDF Debug — Raw Lines' below to inspect "
            "what was extracted from your file."
        )

    df = pd.DataFrame(rows)
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0).round(2)
    return df[['invoice_no', 'amount']], raw_lines

# -------------------------------------------------
# 4. CONTROLLED FUZZY MATCH
# -------------------------------------------------
def perform_fuzzy_check(recon_df, internal_df, threshold=90):
    choices = internal_df['clean_id'].dropna().unique().tolist()

    for idx, row in recon_df.iterrows():
        if (
            row['status'] == "Missing in Books"
            and row['As per Vendor'] > 0
            and len(str(row['Invoice Number'])) >= 6
            and choices
        ):
            match, score = process.extractOne(
                str(row['Invoice Number']), choices
            )
            if score >= threshold:
                recon_df.at[idx, 'status'] = (
                    f"Suggested Match: {match} ({score}%)"
                )
    return recon_df

# -------------------------------------------------
# 5. STREAMLIT UI
# -------------------------------------------------
st.set_page_config(page_title="Vendor Reconciliation Dashboard", layout="wide")
st.title("📑 Vendor Reconciliation Dashboard")

c1, c2 = st.columns(2)
with c1:
    v_file = st.file_uploader("Upload Vendor Statement (PDF / Excel)", type=["pdf", "xlsx"])
with c2:
    i_file = st.file_uploader("Upload Internal Statement (Excel)", type=["xlsx"])

# -------------------------------------------------
# 6. MAIN LOGIC
# -------------------------------------------------
if v_file and i_file:
    with st.spinner("Reconciling vendor invoices..."):

        raw_lines = []

        try:
            # Vendor
            if v_file.name.lower().endswith(".pdf"):
                df_vendor_raw, raw_lines = extract_vendor_pdf(v_file, debug=True)

                # Show a warning if any rows were matched by the lenient fallback
                lenient_count = (df_vendor_raw.get("match_type", pd.Series()) == "lenient").sum() \
                    if "match_type" in df_vendor_raw.columns else 0
                if lenient_count:
                    st.warning(
                        f"⚠️ {lenient_count} invoice(s) were matched using a lenient pattern "
                        "because they didn't match the expected FedEx format. "
                        "Please verify these rows in the report."
                    )
            else:
                df_vendor_raw = pd.read_excel(v_file)

            df_internal_raw = pd.read_excel(i_file)

            vendor   = clean_vendor_data(df_vendor_raw)
            internal = clean_internal_data(df_internal_raw)

            # Reconciliation
            recon = pd.merge(
                vendor,
                internal,
                on="clean_id",
                how="outer",
                suffixes=("_vendor", "_internal")
            )

            recon['Variance'] = (
                recon['clean_amount_vendor'].fillna(0)
                - recon['clean_amount_internal'].fillna(0)
            ).round(2)

            def get_status(row):
                if pd.isna(row['clean_amount_vendor']):
                    return "Missing in Vendor"
                if pd.isna(row['clean_amount_internal']):
                    return "Missing in Books"
                if abs(row['Variance']) > 0.05:
                    return "Amount Mismatch"
                return "Matched"

            recon['status'] = recon.apply(get_status, axis=1)

            recon = recon.rename(columns={
                'clean_id':             'Invoice Number',
                'clean_amount_vendor':  'As per Vendor',
                'clean_amount_internal':'As per Books'
            })

            recon = perform_fuzzy_check(recon, internal)

            # Buckets
            other_exceptions_df = recon[~recon['status'].isin(['Missing in Vendor', 'Matched'])]
            missing_vendor_df   = recon[recon['status'] == 'Missing in Vendor']
            matched_df          = recon[recon['status'] == 'Matched']

            # Dashboard
            st.subheader("⚠️ Other Exceptions")
            st.dataframe(other_exceptions_df, use_container_width=True)

            st.subheader("❌ Missing in Vendor")
            st.dataframe(missing_vendor_df, use_container_width=True)

            with st.expander("✅ View Fully Matched Invoices"):
                st.dataframe(matched_df, use_container_width=True)

            # Export
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                other_exceptions_df.to_excel(writer, index=False, sheet_name="Other_Exceptions")
                missing_vendor_df.to_excel(writer,   index=False, sheet_name="Missing_in_Vendor")
                matched_df.to_excel(writer,           index=False, sheet_name="Matched")
                recon.to_excel(writer,                index=False, sheet_name="Full_Recon")

            st.download_button(
                "📥 Download Reconciliation Report",
                buffer.getvalue(),
                "vendor_reconciliation_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        except ValueError as e:
            st.error(f"❌ {e}")

        # Always show raw PDF lines when a PDF was uploaded (helps diagnose failures)
        if raw_lines:
            with st.expander("🔍 PDF Debug — Raw Extracted Lines (click to inspect)"):
                st.caption(
                    "These are the exact lines pdfplumber read from your PDF. "
                    "If the invoice lines look different from the expected format, "
                    "share a few lines here so the regex can be updated to match."
                )
                st.code("\n".join(raw_lines), language="text")
