"""Shareable workspace bundles — save/restore full workspace state as .sweet files.

A .sweet bundle is a ZIP archive containing:
- manifest.json  — metadata, sheet list, schema, creation info
- data/<name>.parquet  — Parquet-serialized data for each sheet
- transforms.json  — transform history per sheet (reproducible steps)
- journal.json  — operation journal (without DataFrame snapshots)
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import polars as pl

BUNDLE_VERSION = 1
BUNDLE_EXTENSION = ".sweet"


def save_bundle(
    workspace: Any,
    path: str | Path,
    *,
    description: str = "",
    include_journal: bool = True,
) -> Path:
    """Save a workspace as a .sweet bundle file.

    Args:
        workspace: The Workspace instance to serialize.
        path: Output file path. Extension .sweet is added if missing.
        description: Optional description for the bundle.
        include_journal: Whether to include operation history.

    Returns:
        Path to the created bundle file.

    Raises:
        ValueError: If the workspace has no sheets or no data.
    """
    path = Path(path)
    if path.suffix != BUNDLE_EXTENSION:
        path = path.with_suffix(BUNDLE_EXTENSION)

    if not workspace.sheet_names:
        raise ValueError("Cannot save an empty workspace — no sheets loaded.")

    # Build manifest
    sheets_meta: list[dict[str, Any]] = []
    for name in workspace.sheet_names:
        sheet = workspace._workbook.sheets[name]
        if sheet.df is None:
            continue
        sheets_meta.append(
            {
                "name": name,
                "shape": list(sheet.df.shape),
                "schema": {col: str(dtype) for col, dtype in sheet.df.schema.items()},
                "n_transforms": len(sheet.transform_steps),
            }
        )

    if not sheets_meta:
        raise ValueError("No sheets with data to save.")

    manifest = {
        "bundle_version": BUNDLE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": description,
        "current_sheet": workspace.current_sheet_name,
        "source_file": getattr(workspace, "_source_file", None),
        "sheets": sheets_meta,
    }

    # Build transforms
    transforms: dict[str, list[dict[str, Any]]] = {}
    for name in workspace.sheet_names:
        sheet = workspace._workbook.sheets[name]
        steps = []
        for step in sheet.transform_steps:
            steps.append(
                {
                    "expr": step.expr,
                    "input_hash": step.input_hash,
                    "output_schema": step.output_schema,
                    "metadata": step.metadata,
                }
            )
        transforms[name] = steps

    # Build journal (without snapshots)
    journal_data: list[dict[str, Any]] = []
    if include_journal:
        for op in workspace.history():
            journal_data.append(
                {
                    "id": op.id,
                    "timestamp": op.timestamp.isoformat(),
                    "kind": op.kind.value,
                    "sheet": op.sheet,
                    "expr": op.expr,
                    "metadata": op.metadata,
                    "input_hash": op.input_hash,
                    "output_hash": op.output_hash,
                }
            )

    # Write ZIP
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("transforms.json", json.dumps(transforms, indent=2))
        zf.writestr("journal.json", json.dumps(journal_data, indent=2))

        for name in workspace.sheet_names:
            sheet = workspace._workbook.sheets[name]
            if sheet.df is not None:
                buf = BytesIO()
                sheet.df.write_parquet(buf)
                zf.writestr(f"data/{name}.parquet", buf.getvalue())

    return path


def load_bundle(path: str | Path) -> dict[str, Any]:
    """Load a .sweet bundle and return its contents.

    Returns a dict that can be used to restore a Workspace:
    - "manifest": bundle metadata
    - "sheets": dict of {name: pl.DataFrame}
    - "transforms": dict of {name: list of transform step dicts}
    - "journal": list of operation dicts

    Args:
        path: Path to the .sweet bundle file.

    Returns:
        Dict with manifest, sheets, transforms, and journal.

    Raises:
        ValueError: If the file is not a valid .sweet bundle.
        FileNotFoundError: If the file doesn't exist.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Bundle not found: {path}")

    try:
        with zipfile.ZipFile(path, "r") as zf:
            # Read manifest
            manifest = json.loads(zf.read("manifest.json"))

            # Read transforms
            transforms = json.loads(zf.read("transforms.json"))

            # Read journal
            journal: list[dict[str, Any]] = []
            if "journal.json" in zf.namelist():
                journal = json.loads(zf.read("journal.json"))

            # Read data files
            sheets: dict[str, pl.DataFrame] = {}
            for entry in zf.namelist():
                if entry.startswith("data/") and entry.endswith(".parquet"):
                    sheet_name = entry.removeprefix("data/").removesuffix(".parquet")
                    buf = BytesIO(zf.read(entry))
                    sheets[sheet_name] = pl.read_parquet(buf)

    except (zipfile.BadZipFile, KeyError) as e:
        raise ValueError(f"Invalid .sweet bundle: {e}") from e

    return {
        "manifest": manifest,
        "sheets": sheets,
        "transforms": transforms,
        "journal": journal,
    }


def inspect_bundle(path: str | Path) -> dict[str, Any]:
    """Inspect a .sweet bundle without fully loading data.

    Returns metadata about the bundle without reading Parquet data.

    Args:
        path: Path to the .sweet bundle file.

    Returns:
        Dict with manifest info, file size, and sheet summaries.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Bundle not found: {path}")

    try:
        with zipfile.ZipFile(path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))

            # Calculate data sizes
            data_sizes: dict[str, int] = {}
            for entry in zf.namelist():
                if entry.startswith("data/") and entry.endswith(".parquet"):
                    sheet_name = entry.removeprefix("data/").removesuffix(".parquet")
                    info = zf.getinfo(entry)
                    data_sizes[sheet_name] = info.file_size

    except (zipfile.BadZipFile, KeyError) as e:
        raise ValueError(f"Invalid .sweet bundle: {e}") from e

    return {
        "manifest": manifest,
        "file_size": path.stat().st_size,
        "data_sizes": data_sizes,
    }
