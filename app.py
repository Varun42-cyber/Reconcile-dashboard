import streamlit as st
import pandas as pd
import pdfplumber
import re
import io
from thefuzz import process

# --- DATA CLEANING ENGINE ---
def clean_data(df):
    df.columns = [str(c).lower().strip().replace(" ", "_") for c in df.columns]
    inv_col = next((c for c in df.columns if 'inv' in c or 'num' in c or 'id' in c), None)
    amt_col = next((c for c in df.columns if 'amt' in c or 'val' in c or 'total' in c or 'amount' in c), None)
    
    if not inv_col or not amt_col:
        return None

    df['clean_id'] = df[inv_col].astype(str).apply(lambda x: re.sub(r'\W+', '', x).upper().lstrip('0'))
    df['clean_amount'] = df[amt_col].astype(str).replace(r'[$,\s]', '', regex=True)
    df['clean_amount'] = pd.to_numeric(df['clean_amount'], errors='coerce').fillna(0).round(2)
    return df[['clean_id', 'clean_amount']]

# --- DASHBOARD UI ---
st.set_page_config(page_title="Recon Tool", layout="wide")
st.title("📊 Instant Reconciliation Dashboard")

v_file = st.file_uploader("Upload Vendor Statement (PDF/Excel)", type=['pdf', 'xlsx'])
i_file = st.file_uploader("Upload Internal Books (Excel)", type=['xlsx'])

if v_file and i_file:
    # Extract Data
    if v_file.name.endswith('.pdf'):
        with pdfplumber.open(v_file) as pdf:
            rows = [row for page in pdf.pages for row in page.extract_table()]
            df_v_raw = pd.DataFrame(rows[1:], columns=rows[0])
    else:
        df_v_raw = pd.read_excel(v_file)
    df_i_raw = pd.read_excel(i_file)

    # Clean & Reconcile
    v_proc = clean_data(df_v_raw)
    i_proc = clean_data(df_i_raw)

    if v_proc is not None and i_proc is not None:
        recon = pd.merge(v_proc, i_proc, on='clean_id', how='outer', suffixes=('_vendor', '_internal'))
        recon['diff'] = (recon['clean_amount_vendor'].fillna(0) - recon['clean_amount_internal'].fillna(0)).round(2)
        
        def get_status(row):
            if pd.isna(row['clean_amount_vendor']): return "❌ Missing in Vendor"
            if pd.isna(row['clean_amount_internal']): return "❓ Missing in Books"
            if abs(row['diff']) > 0.05: return "⚠️ Amount Mismatch"
            return "✅ Matched"

        recon['status'] = recon.apply(get_status, axis=1)

        # Show Results
        st.divider()
        st.dataframe(recon, use_container_width=True)

        # Excel Export
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            recon.to_excel(writer, index=False)
        st.download_button("Download Excel Report", buffer.getvalue(), "report.xlsx")