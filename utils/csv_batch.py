"""
CSV batch parsing for BodhiFlow.

Parses a CSV file into a list of jobs; each job can override styles, language, output_subdir.
Encoding: UTF-8 only (with or without BOM). Styles must match GUI refinement style labels exactly.
"""

import csv
import io
from pathlib import Path
from typing import Any

from utils.logger_config import get_logger

logger = get_logger(__name__)


def _get_valid_style_names() -> set:
    """Return set of valid refinement style names (must match GUI/prompts)."""
    try:
        from prompts import text_refinement_prompts
        return set(text_refinement_prompts.keys())
    except Exception:
        return set()


def parse_bodhiflow_csv(csv_path: str) -> list[dict[str, Any]]:
    """
    Parse a BodhiFlow batch CSV into a list of jobs.

    Columns (header case-insensitive): input, styles, language, output_subdir, run_phase_1, run_phase_2.
    - input: required (URL or path)
    - styles: optional, comma-separated; must match GUI style labels exactly
    - language, output_subdir: optional
    - run_phase_1, run_phase_2: optional (1/0 or true/false); ignored in GUI CSV mode

    Encoding: UTF-8 only. Other encodings raise ValueError with message to re-save as UTF-8.

    Returns:
        List of dicts: [{"job_id": 1, "input": str, "styles": list[str]|None, "language": str|None, "output_subdir": str|None, ...}, ...]
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    valid_styles = _get_valid_style_names()

    # Read with UTF-8 (allow BOM)
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            text = raw.decode("utf-8-sig")
        else:
            text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(
            "CSV encoding is not UTF-8. Please save the CSV as UTF-8 (e.g. in Excel: Save As -> CSV UTF-8)."
        ) from e

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    # Normalize column names to lower case for lookup
    fieldnames = [f.strip().lower() for f in reader.fieldnames]
    if "input" not in fieldnames:
        raise ValueError("CSV must have an 'input' column.")

    jobs = []
    for row_idx, row in enumerate(reader, start=2):
        # Build row dict with normalized keys
        raw_row = {k.strip().lower(): v.strip() if isinstance(v, str) else v for k, v in row.items()}
        input_val = raw_row.get("input", "").strip()
        if not input_val:
            raise ValueError(f"Row {row_idx}: 'input' is required and cannot be empty.")

        styles_raw = raw_row.get("styles", "").strip()
        styles_list = None
        if styles_raw:
            parts = [p.strip() for p in styles_raw.split(",") if p.strip()]
            for part in parts:
                if part not in valid_styles:
                    raise ValueError(
                        f"Row {row_idx}: style '{part}' is not valid. "
                        f"Styles must match GUI options exactly. Valid: {sorted(valid_styles)}"
                    )
            styles_list = parts

        language = raw_row.get("language", "").strip() or None
        output_subdir = raw_row.get("output_subdir", "").strip() or None
        run_phase_1 = raw_row.get("run_phase_1", "").strip().lower() in ("1", "true", "yes")
        run_phase_2 = raw_row.get("run_phase_2", "").strip().lower() in ("", "1", "true", "yes")

        job_id = len(jobs) + 1
        jobs.append({
            "job_id": job_id,
            "input": input_val,
            "styles": styles_list,
            "language": language,
            "output_subdir": output_subdir,
            "run_phase_1": run_phase_1,
            "run_phase_2": run_phase_2,
        })

    logger.info(f"Parsed CSV: {len(jobs)} jobs from {csv_path}")
    return jobs
