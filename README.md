# tg-claims-fetch

Download **Claims & Objections** PDFs from the Telangana CEO website
(<https://ceotserms2.telangana.gov.in/claimsobjections/claims_objections.aspx>)
for a date range, and validate that each PDF really covers the requested date.

The website only offers one PDF per search in the browser. This tool automates
the search and saves one PDF per (form type, constituency, date), with a
`manifest.csv` report of the validation results.

## Install

```bash
pip install -r requirements.txt   # requests and pypdf
```

## Usage

```bash
# Ask interactively for the date range, download ALL districts / ACs / forms
python3 tg_claims_fetch.py

# Date range, one district by name, all constituencies, all form types
python3 tg_claims_fetch.py --from 2026-08-17 --to 2026-08-20 --district Hyderabad

# One district, one constituency (name or number), two form types
python3 tg_claims_fetch.py --from 2026-08-18 --to 2026-08-18 \
    --district 17 --ac Musheerabad --form form9,form10

# Show the plan (number of PDFs, estimated time) without downloading
python3 tg_claims_fetch.py --from 2026-08-17 --to 2026-08-19 --dry-run

# List all districts and constituencies with their codes
python3 tg_claims_fetch.py --list
```

### Options

| Option | Meaning | Default |
|---|---|---|
| `--from` | From date (`yyyy-mm-dd`) | asked interactively |
| `--to` | To date (`yyyy-mm-dd`) | same as `--from` |
| `--district` | District name or number | all districts |
| `--ac` | Assembly constituency name or number | all constituencies |
| `--form` | `form9,form10,form11,form11a,form11b,form9a` | all form types |
| `--outdir` | Output directory | `./claims_pdfs` |
| `--delay` | Seconds between requests | `3` |
| `--dry-run` | Print the plan and exit | |
| `--yes` | Skip the confirmation question | |
| `--list` | Print all districts and constituencies | |

## Output

- One PDF per search: `form9_AC57_2026-08-18.pdf`
- `manifest.csv` with one row per search: date, district, constituency,
  form type, file, the `From date` / `To date` found in the PDF header,
  the number of rows on that date, and the status.

Status values: `OK`, `DATE MISMATCH`, `NO DATA` (no records on that date),
`FAILED: <reason>`, or a note when pypdf is not installed.

## Validation

For every PDF the tool checks:

1. The `From date` and `To date` in the PDF header match the requested date.
2. It counts the data rows that carry that receipt date.

## Notes

- Dates must be `yyyy-mm-dd`. The website rejects `dd-mm-yyyy`, even though its
  date picker displays that format.
- The published claims period on the site is 17.08.2026 to 16.09.2026, but the
  database contains records from earlier dates (Form 9 records exist at least
  back to 26.07.2026). The tool accepts any date. Empty dates return `NO DATA`.
- The website has an invalid TLS certificate chain. The tool warns and
  continues without certificate verification (same as `curl -k`).
- Runs of more than 100 PDFs ask for confirmation. Use `--yes` to skip this.
- Already downloaded files are skipped, so a stopped run can resume.
- Keep `--delay` at 3 seconds or more. This is a public government service.
  Do not overload it.
