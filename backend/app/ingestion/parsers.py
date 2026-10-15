"""Evidence File Parsers for CSV and JSONL."""

import csv
import io
import json
from typing import Any, Dict, List, Tuple

from app.ingestion.exceptions import DataValidationError, UnsupportedFormatError


def detect_file_type(filename: str, explicit_type: str | None = None) -> str:
    """Detect file type ('CSV' or 'JSONL') from filename or explicit argument."""
    if explicit_type:
        t = explicit_type.strip().upper()
        if t in ("CSV", "JSONL"):
            return t
        if t in ("JSON", "NDJSON"):
            return "JSONL"

    fn = filename.lower()
    if fn.endswith(".csv"):
        return "CSV"
    if fn.endswith(".jsonl") or fn.endswith(".ndjson"):
        return "JSONL"
    if fn.endswith(".json"):
        # For our ingestion, .json containing line-delimited records or array is treated as JSONL
        return "JSONL"

    raise UnsupportedFormatError(
        f"Unsupported file format for '{filename}'. Only CSV and JSONL (.jsonl, .ndjson, .csv) are supported."
    )


def decode_content(content_bytes: bytes) -> str:
    """Decode raw bytes into string safely, supporting UTF-8 and UTF-8-SIG (Excel BOM)."""
    if not content_bytes or len(content_bytes.strip()) == 0:
        raise UnsupportedFormatError("Uploaded evidence file is empty.")

    try:
        return content_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return content_bytes.decode("utf-8")
        except UnicodeDecodeError as err:
            raise UnsupportedFormatError(
                f"File encoding error: Evidence file must be valid UTF-8 encoded text. (Byte position: {err.start})"
            )


def parse_csv(content_str: str) -> List[Dict[str, Any]]:
    """Parse CSV text into list of row dictionaries."""
    f = io.StringIO(content_str)
    reader = csv.reader(f)

    try:
        header_row = next(reader, None)
    except Exception as err:
        raise DataValidationError(f"Failed to read CSV headers: {str(err)}")

    if not header_row:
        raise DataValidationError("CSV file is empty or missing a header row.")

    # Clean headers (strip whitespace and BOM artifacts)
    headers = [h.strip() for h in header_row if h is not None]
    if not headers or all(len(h) == 0 for h in headers):
        raise DataValidationError("CSV file header row contains no valid column names.")

    records: List[Dict[str, Any]] = []
    line_number = 1  # 1 is header

    for row in reader:
        line_number += 1
        # Skip completely empty trailing lines
        if not row or all(len(c.strip()) == 0 for c in row):
            continue

        if len(row) != len(headers):
            raise DataValidationError(
                f"CSV row {line_number} column count mismatch: expected {len(headers)} columns, got {len(row)}.",
                row_index=line_number,
            )

        row_dict = {headers[i]: row[i].strip() for i in range(len(headers))}
        records.append(row_dict)

    if not records:
        raise DataValidationError("CSV file contains a header row but zero data records.")

    return records


def parse_jsonl(content_str: str) -> List[Dict[str, Any]]:
    """Parse JSONL (Newline-Delimited JSON) text into list of dictionaries."""
    lines = content_str.splitlines()
    records: List[Dict[str, Any]] = []
    line_number = 0

    for raw_line in lines:
        line_number += 1
        line = raw_line.strip()
        if not line:
            continue  # Skip blank lines

        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as err:
            raise DataValidationError(
                f"Malformed JSON on line {line_number}: {err.msg}.",
                row_index=line_number,
            )

        if not isinstance(parsed, dict):
            raise DataValidationError(
                f"Invalid JSON record on line {line_number}: each line must be a JSON object (got {type(parsed).__name__}).",
                row_index=line_number,
            )

        records.append(parsed)

    if not records:
        raise DataValidationError("JSONL file contains zero valid JSON records.")

    return records


def parse_evidence_file(content_bytes: bytes, filename: str, explicit_type: str | None = None) -> Tuple[str, List[Dict[str, Any]]]:
    """Orchestrates decoding and parsing based on file format.

    Returns:
        (file_type, records)
    """
    file_type = detect_file_type(filename, explicit_type)
    content_str = decode_content(content_bytes)

    if file_type == "CSV":
        records = parse_csv(content_str)
    elif file_type == "JSONL":
        records = parse_jsonl(content_str)
    else:
        raise UnsupportedFormatError(f"Unsupported file type: {file_type}")

    return file_type, records
