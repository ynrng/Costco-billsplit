#!/usr/bin/env python3
"""Extract product_name,total_price,category from photographed receipts.

Uses Apple's on-device Vision OCR through receipt_ocr.swift (macOS).
Python needs only its standard library. See README.md for usage.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from decimal import Decimal
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata


CSV_FIELDS = ["product_name", "total_price", "category"]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
# Normalize lookalike characters without guessing an unreadable price/category.
LOOKALIKES = str.maketrans({"А": "A", "Α": "A", "Ζ": "Z", "−": "-", "–": "-", "—": "-"})
MONEY = re.compile(r"(?<![\d.,])(?P<leading>-?)\s*(?P<amount>\d+(?:[.,]\d{2}))\s*(?P<trailing>-?)(?![\d.,])")
TOTAL = re.compile(r"^\s*(?P<leading>-?)\s*(?P<amount>\d+[.,]\d{2})\s*(?P<trailing>-?)\s*(?P<category>[^\s.,\d][^\s]*|[27])\s*$")
SUMMARY = re.compile(r"TOTAL|NUMBER\s+OF|ITEMS\s+SOLD|\bBOB\b|\bCOUNT\b|TERNATIONAL|SUBTOTAL|BALANCE|CHANGE|TENDER|VISA|MASTERCARD", re.I)
OWNER_PREFIX = re.compile(r"^(?:[AYBCV](?:\s*[+&/]\s*[AYBCV])*\s+)+(?=[A-Z0-9])", re.I)


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).translate(LOOKALIKES).split())


def parse_total(text: str) -> tuple[str, str] | None:
    """Read ONLY the final-price column; 2.50-Z and -2.50 Z both mean -2.50."""
    match = TOTAL.fullmatch(normalize(text).upper())
    if not match:
        return None
    amount = Decimal(match["amount"].replace(",", "."))
    if match["leading"] or match["trailing"]:
        amount = -amount
    # Return an unknown category as blank, so it cannot silently become A or Z.
    category = match["category"] if match["category"] in {"A", "Z"} else ""
    return f"{amount:.2f}", category


@dataclass
class Observation:
    text: str
    x: float
    y: float
    width: float
    height: float
    confidence: float = 1.0

    @property
    def cy(self) -> float:
        return self.y + self.height / 2


@dataclass
class Item:
    source_file: str
    item_index: int
    product_name: str
    total_price: str
    category: str
    raw_total: str
    issues: list[str] = field(default_factory=list)

    def csv_row(self) -> dict[str, str]:
        return {key: getattr(self, key) for key in CSV_FIELDS}


def is_product(text: str) -> bool:
    text = normalize(text)
    if SUMMARY.search(text):
        return False
    if MONEY.search(text) and re.search(r"\b\d+\s*[x×]\b", text, re.I):
        return False
    if re.fullmatch(r"[\d\s.,:+\-xX×ISEBOZ]+", text):
        return False  # item ID, quantity, or a partial price line
    return len(re.findall(r"[A-Za-z]", text)) >= 3


def merge_line_fragments(observations: list[Observation]) -> list[Observation]:
    """Join words on the same printed line, including detached minus/category."""
    groups: list[list[Observation]] = []
    for obs in sorted(observations, key=lambda o: o.cy):
        matching = [g for g in groups
                    if abs(obs.cy - g[0].cy) < max(obs.height, g[0].height) * 0.6
                    and all(min(obs.x + obs.width, o.x + o.width) - max(obs.x, o.x)
                            < min(obs.width, o.width) * 0.15 for o in g)]
        if matching:
            min(matching, key=lambda g: abs(obs.cy - g[0].cy)).append(obs)
        else:
            groups.append([obs])
    result = []
    for group in groups:
        group.sort(key=lambda o: o.x)
        left, top = min(o.x for o in group), min(o.y for o in group)
        right = max(o.x + o.width for o in group)
        bottom = max(o.y + o.height for o in group)
        result.append(Observation(" ".join(o.text for o in group), left, top, right-left, bottom-top, min(o.confidence for o in group)))
    return sorted(result, key=lambda o: o.cy)


def extract_items(source: str, raw: list[dict], total_column: float = 0.70) -> tuple[list[Item], list[dict]]:
    observations = [Observation(**entry) for entry in raw]
    totals = merge_line_fragments([o for o in observations if o.x >= total_column])
    left_lines = merge_line_fragments([o for o in observations if o.x < total_column])
    names = [o for o in left_lines if is_product(o.text)]
    items: list[Item] = []
    issues: list[dict] = []
    used_names: set[int] = set()
    previous_y = -1.0
    for total in totals:
        # A count (e.g. 87) in a summary is not a product total. Keep malformed
        # decimal amounts for review rather than silently dropping them.
        if not re.search(r"\d+[.,]\d", total.text):
            continue
        parsed = parse_total(total.text)
        price, category = parsed or ("", "")
        notes = []
        if not parsed:
            notes.append("Cannot read final price/category; check raw_total")
        elif not category:
            notes.append("Category is not clearly A or Z")
        # Name and price rows alternate. A small tolerance accounts for tilted
        # photos where the right edge of a row is higher than the left edge.
        candidates = [(i, o) for i, o in enumerate(names) if i not in used_names
                      and previous_y < o.cy < total.cy + total.height * 0.25
                      and total.cy - o.cy < 0.075]
        if candidates:
            index, name = candidates[-1]
            used_names.add(index)
            product_name = OWNER_PREFIX.sub("", normalize(name.text).lstrip("•* +|/_-")).strip(" +|_")
            if name.confidence < 0.85:
                notes.append("Low OCR confidence in product name")
            if len(candidates) > 1:
                notes.append("Multiple possible product-name lines; check association")
        else:
            product_name = ""
            notes.append("No product name found above total")
        if total.confidence < 0.85:
            notes.append("Low OCR confidence in final price/category")
        items.append(Item(source, len(items)+1, product_name, price, category, total.text, notes))
        previous_y = total.cy

    for i, name in enumerate(names):
        if i not in used_names:
            issues.append({"source_file": source, "item_index": "", "product_name": name.text,
                           "total_price": "", "category": "", "raw_total": "",
                           "issue": "Unmatched product-name line: possible missing/cropped price"})
    if not items:
        issues.append({"source_file": source, "item_index": "", "product_name": "",
                       "total_price": "", "category": "", "raw_total": "",
                       "issue": "No product totals detected; check photo and column settings"})
    return items, issues


def apply_corrections(items: list[Item], path: Path | None) -> None:
    """Optional, human-reviewed replacements, keyed by file and product index."""
    if path is None:
        return
    by_key = {(item.source_file, str(item.item_index)): item for item in items}
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"source_file", "item_index", *CSV_FIELDS}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"Corrections CSV must include: {', '.join(sorted(required))}")
        for row in reader:
            key = (row["source_file"], row["item_index"])
            if key not in by_key or key in seen:
                raise ValueError(f"Unknown or duplicate correction: {key}")
            if row["category"] not in {"A", "Z"} or not row["product_name"].strip():
                raise ValueError(f"Correction requires a product name and A/Z category: {key}")
            if not re.fullmatch(r"-?\d+\.\d{2}", row["total_price"]):
                raise ValueError(f"Correction requires a price such as -2.50: {key}")
            item = by_key[key]
            for column in CSV_FIELDS:
                setattr(item, column, row[column])
            item.issues = []
            seen.add(key)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 BOM lets Excel open product names correctly.
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_ocr(images: list[Path], cache: Path, left_crop: float) -> None:
    if sys.platform != "darwin" or not shutil.which("swift"):
        raise RuntimeError("OCR requires macOS and Swift (install with: xcode-select --install).")
    helper = Path(__file__).with_name("receipt_ocr.swift")
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="receipt-swift-") as temporary:
        command = ["swift", "-module-cache-path", str(Path(temporary).resolve()), str(helper),
                   str(cache), str(left_crop), *map(str, images)]
        result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Local OCR failed. Run from Terminal if a sandbox blocks Apple Vision.\n" + result.stderr[:3000])
    for photo in images:
        path = cache / (photo.name + ".json")
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(source_size=photo.stat().st_size, source_mtime_ns=photo.stat().st_mtime_ns)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if result.stdout.strip():
        print(result.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", type=Path, default=Path(__file__).resolve().parent,
                        help="Folder containing receipt photos (default: script's folder)")
    parser.add_argument("-o", "--output", type=Path, help="CSV path (default: FOLDER/receipts.csv)")
    parser.add_argument("--left-crop", type=float, default=0.18,
                        help="Fraction of image width occupied by handwritten margin (default: 0.18)")
    parser.add_argument("--total-column", type=float, default=0.70,
                        help="Final-price column's left edge, fraction of original width (default: 0.70)")
    parser.add_argument("--reuse-ocr", action="store_true", help="Use saved OCR JSON instead of re-reading images")
    parser.add_argument("--corrections", type=Path, help="Optional reviewed replacements CSV; see README")
    args = parser.parse_args()
    folder = args.folder.expanduser().resolve()
    if not 0 <= args.left_crop < args.total_column < 1:
        parser.error("Require 0 <= left-crop < total-column < 1")
    if not folder.is_dir():
        parser.error(f"Not a folder: {folder}")
    images = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS), key=lambda p: p.name.lower())
    if not images:
        parser.error(f"No receipt images found in {folder}")
    output = (args.output or folder / "receipts.csv").expanduser().resolve()
    if output.suffix.lower() != ".csv":
        parser.error("Output must have a .csv extension")
    if args.corrections:
        args.corrections = args.corrections.expanduser().resolve()
        generated = {output, output.with_name(output.stem + "_audit.csv"),
                     output.with_name(output.stem + "_review.csv")}
        if args.corrections in generated:
            parser.error("Keep corrections in a separate file from generated CSV files")
    cache = folder / "ocr_text"
    try:
        if not args.reuse_ocr:
            print(f"Reading {len(images)} receipt images locally...", flush=True)
            run_ocr(images, cache, args.left_crop)
        items: list[Item] = []
        reviews: list[dict] = []
        for photo in images:
            data = json.loads((cache / (photo.name + ".json")).read_text(encoding="utf-8"))
            if data.get("coordinate_system") != "full_image":
                raise ValueError(f"Outdated OCR cache for {photo.name}. Re-run without --reuse-ocr.")
            if data["left_crop"] != args.left_crop:
                raise ValueError("Saved OCR uses a different --left-crop. Re-run without --reuse-ocr.")
            if data.get("source_size") != photo.stat().st_size or data.get("source_mtime_ns") != photo.stat().st_mtime_ns:
                raise ValueError(f"Image changed since OCR: {photo.name}. Re-run without --reuse-ocr.")
            extracted, unmatched = extract_items(photo.name, data["observations"], args.total_column)
            items.extend(extracted)
            reviews.extend(unmatched)
            print(f"  {photo.name}: {len(extracted)} items")
        apply_corrections(items, args.corrections)
        for item in items:
            if item.issues:
                reviews.append({"source_file": item.source_file, "item_index": item.item_index,
                                **item.csv_row(), "raw_total": item.raw_total, "issue": "; ".join(item.issues)})
        write_csv(output, CSV_FIELDS, [item.csv_row() for item in items])
        # The audit file identifies every row's source without adding columns to
        # the requested 3-column CSV. It can also be used as corrections.
        audit = output.with_name(output.stem + "_audit.csv")
        write_csv(audit, ["source_file", "item_index", *CSV_FIELDS, "raw_total"],
                  [{"source_file": i.source_file, "item_index": i.item_index, **i.csv_row(), "raw_total": i.raw_total} for i in items])
        review_path = output.with_name(output.stem + "_review.csv")
        write_csv(review_path, ["source_file", "item_index", *CSV_FIELDS, "raw_total", "issue"], reviews)
        print(f"Wrote {len(items)} rows to {output}")
        print(f"Review flags: {len(reviews)} ({review_path.name}). OCR can also make unflagged mistakes; check against photos.")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
