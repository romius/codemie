# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""XLSX workers for multiprocessing."""

import io
import logging
from typing import Dict, List, Optional, BinaryIO
import openpyxl
import pandas as pd
import python_calamine
from python_calamine import SheetVisibleEnum
from markitdown.converters import HtmlConverter

logger = logging.getLogger(__name__)


def _get_visible_sheets(binary_content: BinaryIO) -> Optional[List[str]]:
    """Get a list of visible sheet names from an Excel file

    Args:
        binary_content: File-like object containing Excel data

    Returns:
        List of visible sheet names or None if an error occurs
    """
    try:
        # Load the workbook with openpyxl to check sheet visibility
        wb = openpyxl.load_workbook(binary_content, read_only=True)
        # Get only visible sheets
        visible_sheet_names = [sheet.title for sheet in wb.worksheets if sheet.sheet_state == 'visible']
        # Reset file pointer for pandas to read
        binary_content.seek(0)
        logger.debug(f"Found {len(visible_sheet_names)} visible sheets: {visible_sheet_names}")
        return visible_sheet_names
    except Exception as e:
        logger.warning(f"Failed to check sheet visibility: {str(e)}. Processing all sheets.")
        return None


def _get_visible_sheets_xlsb(binary_content: bytes) -> Optional[List[str]]:
    """Return names of visible sheets in an xlsb workbook. Returns None on failure."""
    try:
        wb = python_calamine.CalamineWorkbook.from_filelike(io.BytesIO(binary_content))
        return [m.name for m in wb.sheets_metadata if m.visible == SheetVisibleEnum.Visible]
    except Exception as e:
        logger.warning(f"xlsb sheet visibility detection failed, loading all sheets: {e}")
        return None


def _load_xlsb_sheets(binary_content: bytes, sheets_to_load: Optional[List[str]]) -> dict[str, pd.DataFrame]:
    """Load xlsb sheets via pandas calamine engine."""
    return pd.read_excel(
        io.BytesIO(binary_content),
        engine="calamine",
        sheet_name=sheets_to_load,
        keep_default_na=True,
        na_filter=True,
    )


def _normalize_column_names(df: pd.DataFrame):
    rename_dict = {
        col: f"Col{col.split(':')[1].strip()}"
        for col in df.columns
        if isinstance(col, str) and col.startswith("Unnamed: ") and ":" in col
    }
    if rename_dict:
        return df.rename(columns=rename_dict)
    return df


def _replace_nan_with_empty(df: pd.DataFrame) -> pd.DataFrame:
    """
    Replace NaN values with empty strings in the DataFrame.

    Args:
        df: DataFrame to process

    Returns:
        DataFrame with NaN values replaced by empty strings
    """
    try:
        # Replace NaN values with empty strings without creating an unnecessary copy
        return df.fillna("")
    except Exception as e:
        logger.warning(f"Failed to replace NaN values: {str(e)}. Using original DataFrame.")
        return df


def _clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Clean a DataFrame by removing empty rows and columns

    Args:
        df: DataFrame to clean

    Returns:
        Cleaned DataFrame with empty rows and columns removed
    """
    # Create mask for empty strings (after stripping whitespace)
    empty_str_mask_cols = df.astype(str).apply(lambda x: x.str.strip() == '')

    # Drop columns where all values are empty (either NaN or empty string)
    df_clean = df.loc[:, ~(empty_str_mask_cols.all())]

    # Drop rows where all values are empty (either NaN or empty string)
    empty_str_mask_rows = df_clean.astype(str).apply(lambda x: x.str.strip() == '', axis=1)
    df_clean = df_clean.loc[~(empty_str_mask_rows.all(axis=1))]

    return df_clean


def _filter_dataframe(
    df: pd.DataFrame, filter_values: Optional[List[str]] = None, filter_mode: str = "exact"
) -> pd.DataFrame:
    """
    Filter DataFrame rows by checking if all filter values are present in the row.
    This works with pivot tables and complex Excel layouts where column structure is not predictable.

    Args:
        df: DataFrame to filter
        filter_values: List of values to search for. Row must contain ALL values (AND logic).
                      Each value can appear in any cell of the row (OR logic within value).
        filter_mode: Matching mode ('exact' - exact match, 'contains' - substring match)

    Returns:
        Filtered DataFrame containing only rows where all filter values are found
    """
    # If no filter specified, return original DataFrame
    if not filter_values or len(filter_values) == 0:
        return df

    try:
        # Normalize filter values to lowercase for case-insensitive comparison
        filter_values_lower = [str(fv).lower() for fv in filter_values]

        # Helper function to check if a cell matches a specific filter value
        def cell_matches_value(cell_value, filter_value_str):
            cell_str = str(cell_value).lower()

            # Skip empty values
            if not cell_str or cell_str == 'nan':
                return False

            # Apply filter based on mode
            if filter_mode == "exact":
                return cell_str == filter_value_str
            elif filter_mode == "contains":
                return filter_value_str in cell_str
            return False

        # Helper function to check if a row contains a specific filter value in any cell
        def row_contains_value(row, filter_value_str):
            return any(cell_matches_value(cell_value, filter_value_str) for cell_value in row)

        # Helper function to check if a row contains ALL filter values (AND logic)
        def row_matches_all_values(row):
            return all(row_contains_value(row, filter_value_str) for filter_value_str in filter_values_lower)

        # Apply filter to each row
        mask = df.apply(row_matches_all_values, axis=1)
        filtered_df = df[mask]

        logger.debug(
            f"Filtered DataFrame: {len(filtered_df)} rows out of {len(df)} matched ALL filter criteria (filter_values={filter_values}, mode='{filter_mode}')"
        )
        return filtered_df

    except Exception as e:
        logger.warning(f"Failed to filter DataFrame: {str(e)}. Returning original DataFrame.")
        return df


def _sheets_to_markdown(sheets: dict, **kwargs) -> str:
    html_converter = HtmlConverter()
    md_content = ""
    for sheet_name, df in sheets.items():
        md_content += f"## {sheet_name}\n"
        html_content = df.to_html(index=False)
        md_content += html_converter.convert_string(html_content, **kwargs).markdown.strip() + "\n\n"
    return md_content.strip()


def process_xlsx_to_markdown(
    file_bytes: bytes,
    sheet_names: Optional[List[str]],
    visible_only: bool,
    file_ext: str = '.xlsx',
) -> str:
    """
    Load XLSX/XLSB file and convert all sheets to markdown.

    Args:
        file_bytes: Excel file content as bytes
        sheet_names: Optional list of sheets to process
        visible_only: If True, only visible sheets
        file_ext: File extension ('.xlsx' or '.xlsb') controls the read engine

    Returns:
        Markdown string with all sheets as tables
    """
    try:
        sheets = load_xlsx(file_bytes, sheet_names, visible_only, True, None, "exact", file_ext)
        return _sheets_to_markdown(sheets)
    except Exception as e:
        logger.error(f"XLSX to markdown failed: {e}")
        raise


def load_xlsx(
    file_bytes: bytes,
    sheet_names: Optional[List[str]] | None,
    visible_only: bool,
    clean_data: bool,
    filter_values: Optional[List[str]],
    filter_mode: str,
    file_ext: str = '.xlsx',
) -> Dict[str, dict]:
    """
    Load XLSX/XLSB file into DataFrames with cleaning and filtering.

    Args:
        file_bytes: Excel file content as bytes
        sheet_names: Optional list of sheets to load
        visible_only: If True, only visible sheets
        clean_data: If True, clean empty rows/cols
        filter_values: Optional row filter values
        filter_mode: Filter mode ('exact' or 'contains')
        file_ext: File extension ('.xlsx' or '.xlsb') controls the read engine

    Returns:
        Dictionary of sheet name to DataFrame
    """
    try:
        if file_ext == '.xlsb':
            visible_sheet_names = _get_visible_sheets_xlsb(file_bytes) if visible_only else None
            sheets_to_load = sheet_names if sheet_names else None
            if visible_only and visible_sheet_names:
                if sheet_names:
                    sheets_to_load = [n for n in sheet_names if n in visible_sheet_names]
                else:
                    sheets_to_load = visible_sheet_names
            sheets = _load_xlsb_sheets(file_bytes, sheets_to_load)
        else:
            binary_content = io.BytesIO(file_bytes)

            visible_sheet_names = None
            if visible_only:
                visible_sheet_names = _get_visible_sheets(binary_content)

            sheets_to_load = sheet_names if sheet_names else None
            if visible_only and visible_sheet_names:
                if sheet_names:
                    sheets_to_load = [name for name in sheet_names if name in visible_sheet_names]
                else:
                    sheets_to_load = visible_sheet_names

            sheets = pd.read_excel(
                binary_content,
                engine="openpyxl",
                sheet_name=sheets_to_load,
                keep_default_na=True,
                na_filter=True,
            )

        processed_sheets = {}
        for sheet_name, df in sheets.items():
            df = _normalize_column_names(_replace_nan_with_empty(df))
            if clean_data:
                df = _clean_dataframe(df)
            df = _filter_dataframe(df, filter_values, filter_mode)
            processed_sheets[sheet_name] = df

        return processed_sheets
    except Exception as e:
        logger.error(f"XLSX loading failed: {e}")
        raise
