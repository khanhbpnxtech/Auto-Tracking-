"""
Thin wrapper around the official `lark-cli` for the two things this tool
needs from a Lark sheet: read it (with REAL row numbers + column letters),
and write a batch of cells.

Row numbers always come from lark-cli's `[row=N]` prefixes and column letters
from `col_indices` — never hand-counted — so a sheet with a title block above
the header, merged cells, or hidden columns still maps correctly.
"""
import csv
import io
import json
import re
import subprocess
import time
from urllib.parse import parse_qs, urlparse

EVENT_NAME_HEADER = "Event name"
ROW_PREFIX_RE = re.compile(r"^\[row=(\d+)\] ")


def run_cli(args, retries=3):
    # Lark's API occasionally drops a TLS handshake ("tls: internal error");
    # those are retried, real API errors (scopes, bad range...) are not.
    for attempt in range(retries):
        proc = subprocess.run(["lark-cli"] + args + ["--format", "json"],
                              capture_output=True, text=True)
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"lark-cli did not return JSON (rc={proc.returncode})\n"
                f"stdout: {proc.stdout[:2000]}\nstderr: {proc.stderr[:2000]}"
            )
        if data.get("ok") is False and data.get("error", {}).get("type") == "network" and attempt < retries - 1:
            time.sleep(1.5)
            continue
        break
    if data.get("ok") is False:
        err = data.get("error", {})
        raise RuntimeError(f"lark-cli error: {err.get('message', data)}\nhint: {err.get('hint', '')}")
    return data


def sheet_id_from_url(url):
    """`...?sheet=a2c85b` -> "a2c85b" (None when the link has no sheet= part)."""
    return (parse_qs(urlparse(url).query).get("sheet") or [None])[0]


def first_sheet_id(url):
    info = run_cli(["sheets", "+workbook-info", "--url", url])
    sheets = [s for s in (info.get("data") or info).get("sheets") or [] if not s.get("is_hidden")]
    for s in sheets:
        if "tracking" in (s.get("sheet_name") or "").lower():
            return s["sheet_id"]
    if sheets:
        return sheets[0]["sheet_id"]
    raise RuntimeError("Không đọc được danh sách sub-sheet (workbook-info rỗng).")


def read_sheet(url, sheet_id):
    result = run_cli(["sheets", "+csv-get", "--url", url, "--sheet-id", sheet_id])
    data = result.get("data", result)
    if data.get("has_more") or data.get("truncated"):
        raise RuntimeError("Sheet quá lớn cho 1 lần đọc (has_more/truncated) — cần đọc theo --range từng lô.")
    return data


def parse_sheet(csv_result):
    """Returns (header_row_num, headers: list[str], col_letter: dict[str,str],
    rows: list[{'row_num': int, 'cells': dict[str,str]}])."""
    col_indices = csv_result["col_indices"]  # position j -> column letter

    # A quoted cell may contain newlines, so one logical row can span several
    # physical lines; only lines starting with "[row=N] " begin a new record.
    records = []
    for line in csv_result["annotated_csv"].split("\n"):
        m = ROW_PREFIX_RE.match(line)
        if m:
            records.append([int(m.group(1)), line[m.end():]])
        elif records:
            records[-1][1] += "\n" + line

    header_row_num = None
    headers = None
    rows = []
    for row_num, rest in records:
        fields = next(csv.reader(io.StringIO(rest)), [])

        if headers is None:
            if EVENT_NAME_HEADER in [f.strip() for f in fields]:
                header_row_num = row_num
                headers = [f.strip() for f in fields]
            continue

        cells = {}
        for j, val in enumerate(fields):
            if j < len(headers) and headers[j]:
                cells[headers[j]] = val
        rows.append({"row_num": row_num, "cells": cells})

    if headers is None:
        raise RuntimeError(f"Không tìm thấy header '{EVENT_NAME_HEADER}' trong sheet.")

    col_letter = {}
    for j, name in enumerate(headers):
        if name and j < len(col_indices):
            col_letter[name] = col_indices[j]
    return header_row_num, headers, col_letter, rows


def write_cells(url, sheet_id, writes, dry_run=True):
    args = ["sheets", "+cells-set", "--url", url, "--sheet-id", sheet_id,
            "--writes", json.dumps(writes, ensure_ascii=False)]
    if dry_run:
        args.append("--dry-run")
    return run_cli(args)
