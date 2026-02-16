import streamlit as st
import pandas as pd
import pdfplumber
import re
import io
from thefuzz import process

# -------------------------------------------------
# 1. CLEAN VENDOR DATA (PDF / EXCEL)
# -------------------------------------------------
def clean_vendor_data(df):
    df = df.copy()
    df.columns = (
        df.columns
        .astype(str)
        .str.strip()
        .str.lower()
        .str.replace(r'\s+', ' ', regex=True)
    )

    inv_col = next(
        (c for c in df.columns if 'invoice' in c or 'inv' in c),
        None
    )

    amt_col = next(
        (c for c in df.columns if 'amount' in c or 'due' in c),
        None
    )

    if not inv_col or not amt_col:
        raise ValueError(f"Vendor columns not detected. Found: {list(df.columns)}")

    df['clean_id'] = (
        df[inv_col]
        .astype(str)
        .str.replace(r'\W+', '', regex=True)
        .str.upper()
        .str.lstrip('0')
    )

    df['clean_amount'] = (
        df[amt_col]
        .astype(str)
        .str.replace(r'[,$\s]', '', regex=True)
    )

    df['clean_amount'] = (
        pd.to_numeric(df['clean_amount'], errors='coerce')
        .fillna(0)
        .round(2)
    )

    return df[['clean_id', 'clean_amount']]

# -------------------------------------------------
# 2. CLEAN INTERNAL DATA (ROBUST HEADER MATCHING)
# -------------------------------------------------
def clean_internal_data(df):
    df = df.copy()

    # Normalize headers
    df.columns = (
        df.columns
        .astype(str)
        .str.strip()
        .str.lower()
        .str.replace(r'\s+', ' ', regex=True)
    )

    # Find invoice number column
    inv_col = next(
        (
            c for c in df.columns
            if 'external' in c and 'document' in c
        ),
        None
    )

    # Find amount ($) column
    amt_co_
