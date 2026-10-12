# Receipt photos to CSV

The six photos in this folder have already been converted to `receipts.csv`:
105 entries, including six negative-price entries. All final prices and A/Z
categories were checked against the photos. `corrections.csv` contains 36
visually checked replacements for OCR spelling/category errors.

To reproduce this checked result quickly from the saved OCR:

```sh
python3 autosplit.py --reuse-ocr --corrections corrections.csv
```

To read receipt photos afresh, run from Terminal on this Mac:

```sh
python3 autosplit.py
```

Add `--corrections corrections.csv` to retain the reviewed replacements for
these same six photos. For a different set of photos, use its own corrections.

The script reads JPEG/JPG, PNG, and TIFF files in the folder, in filename order,
and creates `receipts.csv` with exactly these columns:

```csv
product_name,total_price,category
TRUFF CHIPS 500G,5.69,A
INDOMIE NOODLES,-2.50,Z
```

It uses the **final price**, ignores item IDs, quantities, and unit prices, and
keeps repeated products as separate rows. Both `-2.50 Z` and the receipt's
trailing-minus notation `2.50-Z` become `-2.50,Z`. It skips summary lines such as
`TOTAL NUMBER OF ITEMS SOLD` and `BOB Count`.

## Requirements

Python 3.10+ and macOS with Swift/Apple command line developer tools. These are
already available on this Mac. No pip packages, API keys, or cloud uploads are
needed. On another Mac, install the developer tools with `xcode-select --install`
if `swift` is missing. The OCR helper `receipt_ocr.swift` must stay beside
`autosplit.py`. This OCR backend does not run on Windows or Linux.

## Checking the result

Faded print and handwriting can cause OCR errors, even when OCR reports high
confidence. Check the CSV against the photos before relying on its totals.

- `receipts_audit.csv` identifies each output row by source filename and item
  index, and includes the original OCR total.
- `receipts_review.csv` lists detected problems, including unreadable categories
  and product names without prices. Unreadable fields are left blank in the main
  CSV, rather than guessed. An empty review file does not guarantee perfect OCR.
- `ocr_text/` contains the recognized text and its positions for each image.
  A picture cut off at the edge can omit an entire row without a review flag.

To correct OCR, copy affected rows from the audit CSV into a separate
`corrections.csv`. Keep the columns `source_file,item_index,product_name,total_price,category`
and replace the three extracted values with the readings from the photo.
Prices must use two decimal places and a leading minus for negative amounts.
Then run:

```sh
python3 autosplit.py --reuse-ocr --corrections corrections.csv
```

Corrections are explicit: they are applied only when `--corrections` is supplied.
The program never changes the original images. Re-running overwrites generated
CSV/OCR files; keep manual edits in the separate corrections file.

## Other folders and layouts

```sh
python3 autosplit.py /path/to/receipts -o /path/to/results.csv
python3 autosplit.py --reuse-ocr
python3 autosplit.py --help
```

These photos have handwritten owner initials in the left margin. By default,
OCR ignores the left 18% of each image, and extraction reads final prices in the
rightmost 30%. Names are paired with the following total by their positions.
For different framing, use `--left-crop 0.15 --total-column 0.70`, for example.
The crop must leave the entire printed product name visible. Owner initials
that overlap printed letters may still need manual correction. Summary counts
are ignored; no deduplication across overlapping photos is attempted.

`--reuse-ocr` requires unchanged source images and the same crop setting. Omit
it when adding/changing images or changing `--left-crop`.

Run parser checks with `python3 -m unittest -v`.
