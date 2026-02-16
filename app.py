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
        .str.replace(r'\s+', ' ', regex=True)
    )

    inv_col = next((c for c in df.columns if 'invoice' in c or 'inv' in c), None)
    amt_col = next((c for c in df.columns if 'amount' in c or 'due' in c), None)

    if not inv_col or not amt_col:
        raise ValueError(f"Vendor columns not detected. Found: {list(df.columns)}")

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
        .str.replace(r'\s+', ' ', regex=True)
    )

    inv_col = next((c for c in df.columns if 'external' in c and 'document' in c), None)
    amt_col = next((c for c in df.columns if 'amount' in c), None)

    if not inv_col or not amt_col:
        raise ValueError(f"Required internal columns not found. Found: {list(df.columns)}")

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
# 3. FUZZY MATCH
# -------------------------------------------------
def perform_fuzzy_check(recon_df, internal_df, threshold=90):
    choices = internal_df['clean_id'].dropna().unique().tolist()

    for idx, row in recon_df.iterrows():
        if row['status'] == "Missing in Books" and choices:
            match, score = process.extractOne(row['Invoice Number'], choices)
            if score >= threshold:
                recon_df.at[idx, 'status'] = f"Suggested Match: {match} ({score}%)"

    return recon_df

# -------------------------------------------------
# 4. STREAMLIT UI
# -------------------------------------------------
st.set_page_config(page_title="FedEx Invoice Reconciliation", layout="wide")
st.title("📑 FedEx Invoice Reconciliation Dashboard")

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

        # -------- Vendor --------
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
                            rows.append([match.group(1), match.group(4)])

            if not rows:
                st.error("No invoice data detected in vendor PDF.")
                st.stop()

            df_vendor_raw = pd.DataFrame(rows, columns=["invoice_no", "amount"])
        else:
            df_vendor_raw = pd.read_excel(v_file)

        df_internal_raw = pd.read_excel(i_file)

        vendor = clean_vendor_data(df_vendor_raw)
        internal = clean_internal_data(df_internal_raw)

        # -------- Reconciliation --------
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
            'clean_id': 'Invoice Number',
            'clean_amount_vendor': 'As per Vendor',
            'clean_amount_internal': 'As per Books'
        })

        recon = perform_fuzzy_check(recon, internal)

        # -------- Split Buckets --------
        missing_vendor_df = recon[recon['status'] == 'Missing in Vendor']
        matched_df = recon[recon['status'] == 'Matched']
        other_exceptions_df = recon[
            ~recon['status'].isin(['Missing in Vendor', 'Matched'])
        ]

        # -------- Dashboard --------
        st.subheader("❌ Missing in Vendor")
        st.dataframe(missing_vendor_df, use_container_width=True)

        st.subheader("⚠️ Other Exceptions")
        st.dataframe(other_exceptions_df, use_container_width=True)

        with st.expander("✅ View Fully Matched Invoices"):
            st.dataframe(matched_df, use_container_width=True)

        # -------- Export --------
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            missing_vendor_df.to_excel(writer, index=False, sheet_name="Missing_in_Vendor")
            other_exceptions_df.to_excel(writer, index=False, sheet_name="Other_Exceptions")
            matched_df.to_excel(writer, index=False, sheet_name="Matched")
            recon.to_excel(writer, index=False, sheet_name="Full_Recon")

        st.download_button(
            "📥 Download Reconciliation Report",
            buffer.getvalue(),
            "fedex_invoice_reconciliation.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
