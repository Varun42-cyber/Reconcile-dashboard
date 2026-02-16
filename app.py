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
    df.columns = [c.lower().strip().replace(" ", "_") for c in df.columns]

    inv_col = next(c for c in df.columns if 'invoice' in c or 'inv' in c)
    amt_col = next(c for c in df.columns if 'amount' in c or 'due' in c)

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

    df['clean_amount'] = pd.to_numeric(df['clean_amount'], errors='coerce').fillna(0).round(2)

    return df[['clean_id', 'clean_amount']]

# -------------------------------------------------
# 2. CLEAN INTERNAL DATA (FIXED COLUMNS)
# -------------------------------------------------
def clean_internal_data(df):
    df = df.copy()

    REQUIRED_COLS = ['External document number', 'Amount ($)']
    missing = [c for c in REQUIRED_COLS if c not in df.columns]

    if missing:
        raise ValueError(f"Missing required internal columns: {missing}")

    df['clean_id'] = (
        df['External document number']
        .astype(str)
        .str.replace(r'\W+', '', regex=True)
        .str.upper()
        .str.lstrip('0')
    )

    df['clean_amount'] = (
        df['Amount ($)']
        .astype(str)
        .str.replace(r'[,$\s]', '', regex=True)
    )

    df['clean_amount'] = pd.to_numeric(df['clean_amount'], errors='coerce').fillna(0).round(2)

    return df[['clean_id', 'clean_amount']]

# -------------------------------------------------
# 3. FUZZY MATCH
# -------------------------------------------------
def perform_fuzzy_check(recon_df, internal_df, threshold=90):
    choices = internal_df['clean_id'].tolist()

    for idx, row in recon_df.iterrows():
        if row['status'] == "❓ Missing in Books" and choices:
            match, score = process.extractOne(row['clean_id'], choices)
            if score >= threshold:
                recon_df.at[idx, 'status'] = f"💡 Suggested Match: {match} ({score}%)"

    return recon_df

# -------------------------------------------------
# 4. STREAMLIT UI
# -------------------------------------------------
st.set_page_config(page_title="FedEx Invoice Reconciliation", layout="wide")
st.title("📑 FedEx Invoice Reconciliation")

c1, c2 = st.columns(2)
with c1:
    v_file = st.file_uploader("Upload FedEx Statement (PDF / Excel)", type=["pdf", "xlsx"])
with c2:
    i_file = st.file_uploader("Upload Internal Statement (Excel)", type=["xlsx"])

# -------------------------------------------------
# 5. MAIN LOGIC
# -------------------------------------------------
if v_file and i_file:
    with st.spinner("Reconciling invoices..."):

        # ----- Vendor -----
        if v_file.name.lower().endswith(".pdf"):
            rows = []

            with pdfplumber.open(v_file) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if not text:
                        continue

                    for line in text.split("\n"):
                        match = re.match(
                            r'(\d{1,2}-\d{3}-\d{5})\s+'
                            r'(Freight|Duty/Tax)\s+'
                            r'\d{2}\s+\w+\s+\d{2}\s+'
                            r'\d+\s+HKD\s+'
                            r'([\d,]+\.\d{2})\s+'
                            r'([\d,]+\.\d{2})',
                            line
                        )

                        if match:
                            rows.append([
                                match.group(1),
                                match.group(4)  # Invoice face value
                            ])

            if not rows:
                st.error("No invoice rows found in FedEx PDF.")
                st.stop()

            df_vendor_raw = pd.DataFrame(rows, columns=["invoice_no", "amount"])

        else:
            df_vendor_raw = pd.read_excel(v_file)

        # ----- Internal -----
        df_internal_raw = pd.read_excel(i_file)

        vendor = clean_vendor_data(df_vendor_raw)
        internal = clean_internal_data(df_internal_raw)

        # ----- Reconciliation -----
        recon = pd.merge(
            vendor,
            internal,
            on="clean_id",
            how="outer",
            suffixes=("_vendor", "_internal")
        )

        recon['difference'] = (
            recon['clean_amount_vendor'].fillna(0)
            - recon['clean_amount_internal'].fillna(0)
        ).round(2)

        def get_status(row):
            if pd.isna(row['clean_amount_vendor']):
                return "❌ Missing in Vendor"
            if pd.isna(row['clean_amount_internal']):
                return "❓ Missing in Books"
            if abs(row['difference']) > 0.05:
                return "⚠️ Amount Mismatch"
            return "✅ Matched"

        recon['status'] = recon.apply(get_status, axis=1)
        recon = perform_fuzzy_check(recon, internal)

        # ----- Output -----
        st.subheader("Reconciliation Result")
        st.dataframe(recon, use_container_width=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            recon.to_excel(writer, index=False, sheet_name="Recon")

        st.download_button(
            "📥 Download Reconciliation",
            buffer.getvalue(),
            "fedex_invoice_reconciliation.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
