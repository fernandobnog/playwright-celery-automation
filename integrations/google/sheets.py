"""
Google Sheets API Service Wrapper using gspread.
Provides typed access, record reading, row appending, and batch updates.
"""

import logging
from typing import Any, Dict, List, Optional, Union
import gspread

from integrations.google.auth import GoogleAuthManager

logger = logging.getLogger(__name__)


class SheetsService:
    """
    Client for Google Sheets API operations using gspread.
    """

    def __init__(self, auth_manager: Optional[GoogleAuthManager] = None):
        self.auth = auth_manager or GoogleAuthManager()
        self._client: Optional[gspread.Client] = None

    def get_client(self) -> gspread.Client:
        """
        Initializes and returns an authorized gspread client.
        """
        creds = self.auth.get_credentials("sheets")
        return gspread.authorize(creds)

    def _get_worksheet(
        self, spreadsheet_id: str, worksheet: Union[int, str] = 0
    ) -> gspread.Worksheet:
        gc = self.get_client()
        sh = gc.open_by_key(spreadsheet_id)
        if isinstance(worksheet, int):
            return sh.get_worksheet(worksheet)
        return sh.worksheet(worksheet)

    def read_records(
        self,
        spreadsheet_id: str,
        worksheet: Union[int, str] = 0,
        expected_headers: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns all records from the worksheet as a list of dictionaries.
        """
        ws = self._get_worksheet(spreadsheet_id, worksheet)
        records = ws.get_all_records(expected_headers=expected_headers)
        logger.info(
            "Read %d records from spreadsheet %s (sheet: %s)",
            len(records),
            spreadsheet_id,
            worksheet,
        )
        return records

    def append_row(
        self,
        spreadsheet_id: str,
        row_values: List[Any],
        worksheet: Union[int, str] = 0,
        value_input_option: str = "USER_ENTERED",
    ) -> Dict[str, Any]:
        """
        Appends a single row to the end of the worksheet.
        """
        ws = self._get_worksheet(spreadsheet_id, worksheet)
        res = ws.append_row(row_values, value_input_option=value_input_option)
        logger.info("Appended row to sheet %s: %s", spreadsheet_id, row_values)
        return res

    def append_rows(
        self,
        spreadsheet_id: str,
        rows_values: List[List[Any]],
        worksheet: Union[int, str] = 0,
        value_input_option: str = "USER_ENTERED",
    ) -> Dict[str, Any]:
        """
        Appends multiple rows in a single batch request.
        """
        ws = self._get_worksheet(spreadsheet_id, worksheet)
        res = ws.append_rows(rows_values, value_input_option=value_input_option)
        logger.info("Appended %d rows to sheet %s", len(rows_values), spreadsheet_id)
        return res

    def update_cell(
        self,
        spreadsheet_id: str,
        row: int,
        col: int,
        value: Any,
        worksheet: Union[int, str] = 0,
    ) -> Dict[str, Any]:
        """
        Updates a specific cell value (1-indexed).
        """
        ws = self._get_worksheet(spreadsheet_id, worksheet)
        return ws.update_cell(row, col, value)

    def find_and_update_row(
        self,
        spreadsheet_id: str,
        search_col: int,
        search_value: str,
        update_col: int,
        new_value: Any,
        worksheet: Union[int, str] = 0,
    ) -> bool:
        """
        Finds a row by value in search_col and updates update_col in that same row.
        """
        ws = self._get_worksheet(spreadsheet_id, worksheet)
        cell = ws.find(str(search_value), in_column=search_col)
        if cell:
            ws.update_cell(cell.row, update_col, new_value)
            return True
        return False
