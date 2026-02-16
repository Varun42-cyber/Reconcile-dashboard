import streamlit as st
import pandas as pd
import pdfplumber
import re
import io
from thefuzz import process

# -------------------------------------------------
# 1. DATA CLEANING ENGINE
# -------------------------------------------------
def clean_data(df):
    df.columns = [str(c).lower().strip().replace(" ", "_") for c in df.columns]

    inv_col = next((c for c in df.columns if 'inv' in c or 'num' in c or 'id' in c or 'ref' in c), None)
    amt_col = next((c for c in df.columns if 'amt' in c or 'amount' in c or 'total' in c or 'due' in c), None)

    if not inv_col or not amt_col:
        return None

    df['clean_id'] = (
        df[inv_col].astype(str)
        .str.replace(r'\W+', '', regex=True)
        .str.upper()
        .str.lstrip('0')
    )

    df['clean_amount'] = (
        df[amt_col].astype(str)
        .str.replace(r'[,$\s]', '', regex=True)
        .str.replace(r'\(', '-', regex=True)
        .str.replace(r'\)', '', regex=True)
    )

    df['clean_amount'] = pd.to_numeric(df['clean_amount'], errors='coerce').fillna(0).round(2)

    return df[['clean_id', 'clean_amount']]

# -------------------------------------------------
# 2. FUZZY MATCH ENGINE
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
# 3. STREAMLIT UI
# -------------------------------------------------
st.set_page_config(page_title="AI Finance Recon", layout="wide")
st.title("📑 Professional Statement Reconciler")
st.markdown("Secure in-memory reconciliation. No data is stored.")

c1, c2 = st.columns(2)
with c1:
    v_file = st.file_uploader("Upload Vendor PDF / Excel", type=['pdf', 'xlsx'])
with c2:
    i_file = st.file_uploader("Upload Internal Books (Excel)", type=['xlsx'])

# -------------------------------------------------
# 4. MAIN LOGIC
# -------------------------------------------------
if v_file and i_file:
    with st.spinner("Processing files..."):

        # ---------- VENDOR FILE ----------
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
                                match.group(3),
                                match.group(4)
                            ])

            if not rows:
                st.error("❌ No invoice data detected in PDF.")
                st.stop()

            df_v_raw = pd.DataFrame(
                rows,
                columns=["invoice_no", "invoice_amount", "amount_due"]
            )

        else:
            df_v_raw = pd.read_excel(v_file)

        # ---------- INTERNAL FILE ----------
        df_i_raw = pd.read_excel(i_file)

        # ---------- CLEANING ----------
        v_proc = clean_data(df_v_raw)
        i_proc = clean_data(df_i_raw)

        if v_proc is None or i_proc is None:
            st.error("❌ Could not identify Invoice or Amount columns.")
            st.stop()

        # ---------- RECONCILIATION ----------
        recon = pd.merge(
            v_proc, i_proc,
            on='clean_id',
            how='outer',
            suffixes=('_vendor', '_internal')
        )

        recon['difference'] = (
            recon['clean_amount_vendor'].fillna(0) -
            recon['clean_amount_internal'].fillna(0)
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

        recon = perform_fuzzy_check(recon, i_proc)

        # ---------- METRICS ----------
        st.divider()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Items", len(recon))
        m2.metric("Matched", (recon['status'] == "✅ Matched").sum())
        m3.metric("Issues", recon['status'].str.contains("❌|⚠️|❓|💡").sum())
        m4.metric("Net Variance", f"HKD {recon['difference'].sum():,.2f}")

        # ---------- DISPLAY ----------
        def color_status(val):
            if "✅" in val:
                return 'background-color:#d4edda'
            if "⚠️" in val or "💡" in val:
                return 'background-color:#fff3cd'
            return 'background-color:#f8d7da'

        st.subheader("Reconciliation Details")
        st.dataframe(
            recon.style.applymap(color_status, subset=['status']),
            use_container_width=True
        )

        # ---------- EXPORT ----------
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            recon.to_excel(writer, index=False, sheet_name="Recon_Report")

        st.download_button(
            "📥 Download Reconciliation Report",
            buffer.getvalue(),
            "reconciliation_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
