import streamlit as st
import pandas as pd
import pdfplumber
import re
import io
from thefuzz import process

# --- 1. THE SUPER-CLEANER ENGINE ---
def clean_data(df):
    """Standardizes data to ensure matches even with dirty input."""
    # Standardize column names
    df.columns = [str(c).lower().strip().replace(" ", "_") for c in df.columns]
    
    # Identify Invoice & Amount columns
    inv_col = next((c for c in df.columns if 'inv' in c or 'num' in c or 'id' in c or 'ref' in c or 'voucher' in c), None)
    amt_col = next((c for c in df.columns if 'amt' in c or 'val' in c or 'total' in c or 'amount' in c or 'price' in c), None)
    
    if not inv_col or not amt_col:
        return None

    # THE FIX: Robust ID Cleaning
    # 1. Convert to string
    # 2. Remove all non-alphanumeric (dashes, dots, spaces)
    # 3. Strip leading zeros (00123 -> 123)
    # 4. Uppercase everything
    df['clean_id'] = (
        df[inv_col].astype(str)
        .str.replace(r'\W+', '', regex=True)
        .str.strip()
        .str.upper()
        .str.lstrip('0')
    )
    
    # Robust Amount Cleaning
    # Remove $, commas, and handle parentheses for negative numbers
    df['clean_amount'] = (
        df[amt_col].astype(str)
        .str.replace(r'[$,\s]', '', regex=True)
        .str.replace(r'\(', '-', regex=True)
        .str.replace(r'\)', '', regex=True)
    )
    df['clean_amount'] = pd.to_numeric(df['clean_amount'], errors='coerce').fillna(0).round(2)
    
    return df[['clean_id', 'clean_amount']]

# --- 2. FUZZY MATCH LOGIC ---
def perform_fuzzy_check(recon_df, internal_df, threshold=90):
    """If an invoice is missing, find the closest typo match."""
    for idx, row in recon_df.iterrows():
        if row['status'] == "❓ Missing in Books":
            # Extract list of available IDs to compare against
            choices = internal_df['clean_id'].tolist()
            if choices:
                match, score = process.extractOne(str(row['clean_id']), choices)
                if score >= threshold:
                    recon_df.at[idx, 'status'] = f"💡 Suggested Match: {match} ({score}%)"
    return recon_df

# --- 3. STREAMLIT DASHBOARD ---
st.set_page_config(page_title="AI Finance Recon", layout="wide")

st.title("📑 Professional Statement Reconciler")
st.markdown("This tool uses **Pandas In-Memory** processing. No data is stored or sent to a database.")

# File Uploaders
c1, c2 = st.columns(2)
with c1:
    v_file = st.file_uploader("Upload Vendor Data (PDF/Excel)", type=['pdf', 'xlsx'])
with c2:
    i_file = st.file_uploader("Upload Internal Books (Excel)", type=['xlsx'])

if v_file and i_file:
    with st.spinner("Synchronizing datasets..."):
        # Extraction
        if v_file.name.endswith('.pdf'):
            with pdfplumber.open(v_file) as pdf:
                all_rows = []
                for page in pdf.pages:
                    table = page.extract_table()
                    if table: all_rows.extend(table)
                df_v_raw = pd.DataFrame(all_rows[1:], columns=all_rows[0])
        else:
            df_v_raw = pd.read_excel(v_file)
            
        df_i_raw = pd.read_excel(i_file)

        # Cleaning
        v_proc = clean_data(df_v_raw)
        i_proc = clean_data(df_i_raw)

        if v_proc is not None and i_proc is not None:
            # Join datasets
            recon = pd.merge(v_proc, i_proc, on='clean_id', how='outer', suffixes=('_vendor', '_internal'))
            
            # Calculate Variance
            recon['difference'] = (recon['clean_amount_vendor'].fillna(0) - recon['clean_amount_internal'].fillna(0)).round(2)
            
            # Initial Status Tagging
            def get_status(row):
                if pd.isna(row['clean_amount_vendor']): return "❌ Missing in Vendor"
                if pd.isna(row['clean_amount_internal']): return "❓ Missing in Books"
                if abs(row['difference']) > 0.05: return "⚠️ Amount Mismatch"
                return "✅ Matched"

            recon['status'] = recon.apply(get_status, axis=1)
            
            # Run Fuzzy Check for typos
            recon = perform_fuzzy_check(recon, i_proc)

            # --- Results Display ---
            st.divider()
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Items", len(recon))
            m2.metric("Matches", len(recon[recon['status'] == "✅ Matched"]))
            m3.metric("Issues Found", len(recon[recon['status'].str.contains("⚠️|❌|❓|💡")]))
            m4.metric("Net Variance", f"${recon['difference'].sum():,.2f}")

            # Styling
            def color_status(val):
                if "✅" in val: return 'background-color: #d4edda'
                if "⚠️" in val or "💡" in val: return 'background-color: #fff3cd'
                return 'background-color: #f8d7da'

            st.subheader("Reconciliation Detail")
            st.dataframe(recon.style.applymap(color_status, subset=['status']), use_container_width=True)

            # --- Export Module ---
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                recon.to_excel(writer, index=False, sheet_name='Recon_Report')
            
            st.download_button(
                label="📥 Download Export for Team",
                data=buffer.getvalue(),
                file_name="reconciled_report.xlsx",
                mime="application/vnd.ms-excel"
            )
        else:
            st.error("Error: Could not identify Invoice or Amount columns. Please check your file headers.")
