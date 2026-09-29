#!/usr/bin/env python3
"""
tg_claims_fetch.py - Download Claims & Objections PDFs from the Telangana CEO site
https://ceotserms2.telangana.gov.in/claimsobjections/claims_objections.aspx
for a date range, and validate that each PDF covers the requested date.

Examples:
  # Ask interactively for the date range, download ALL districts / ACs / forms
  python3 tg_claims_fetch.py

  # Date range, one district by name, all constituencies, all forms
  python3 tg_claims_fetch.py --from 2026-08-17 --to 2026-08-20 --district Hyderabad

  # One district, one constituency (name or number), two form types
  python3 tg_claims_fetch.py --from 2026-08-18 --to 2026-08-18 \
      --district 17 --ac Musheerabad --form form9,form10

  # Show the plan without downloading
  python3 tg_claims_fetch.py --from 2026-08-17 --to 2026-08-19 --dry-run

Notes:
  - Dates must be ISO format: yyyy-mm-dd. The server rejects dd-mm-yyyy.
  - The published claims period is 17.08.2026 to 16.09.2026.
  - No --form means all form types. No --district means all districts.
    No --ac means all constituencies of the selected districts.
  - Keep --delay at 3 seconds or more. Do not overload the government server.
  - Requires: pip install requests pypdf (pypdf is optional; skip validation without it)
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime, timedelta

import requests

try:
    import pypdf
except ImportError:
    pypdf = None

BASE_URL = "https://ceotserms2.telangana.gov.in/claimsobjections/"
PAGE_URL = BASE_URL + "claims_objections.aspx"
PDF_URL = BASE_URL + "PDFGeneration.aspx"

PREFIX = "ctl00$ContentPlaceHolder1$"
FORM_TYPES = {
    "form9": "Form9",
    "form10": "Form10",
    "form11": "Form11",
    "form11a": "Form11A",
    "form11b": "Form11B",
    "form9a": "Form 9 (Advance Claim & Objection)",
}
REQUEST_TIMEOUT = 120


# ---------------------------------------------------------------- helpers

def hidden_fields(html):
    """Extract ASP.NET hidden tokens (__VIEWSTATE etc.) from page HTML."""
    out = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        m = re.search(r'name="%s" id="[^"]*" value="([^"]*)"' % name, html)
        if not m:
            raise RuntimeError("Could not find %s in page. The site layout may have changed." % name)
        out[name] = m.group(1)
    return out


def parse_select(html, control):
    """Return [(value, label), ...] for one ASP.NET dropdown (ddlDist / ddlAC)."""
    m = re.search(r'<select[^>]*%s[^>]*>(.*?)</select>' % control, html, re.S)
    if not m:
        return []
    return [(v, lbl.strip()) for v, lbl in
            re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', m.group(1))]


def match_options(options, needle, kind):
    """
    Match user input against dropdown options.
    Accepts the numeric value, the full label ('17-Hyderabad'), the bare name
    ('Hyderabad'), or a unique case-insensitive substring.
    """
    needle = needle.strip().casefold()
    if not needle:
        return []
    # exact value or exact label
    for v, lbl in options:
        if v.casefold() == needle or lbl.casefold() == needle:
            return [(v, lbl)]
    # label without the leading 'N-' numbering
    for v, lbl in options:
        bare = re.sub(r'^\d+-', '', lbl).casefold()
        if bare == needle:
            return [(v, lbl)]
    # unique substring match on the bare name
    hits = [(v, lbl) for v, lbl in options
            if needle in re.sub(r'^\d+-', '', lbl).casefold()]
    if len(hits) == 1:
        return hits
    if len(hits) > 1:
        sys.exit("ERROR: %s '%s' is ambiguous. Matches: %s"
                 % (kind, needle, ", ".join(lbl for _, lbl in hits)))
    return []


def date_range(d1, d2):
    d = datetime.strptime(d1, "%Y-%m-%d")
    end = datetime.strptime(d2, "%Y-%m-%d")
    while d <= end:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def validate_pdf(path, date_iso):
    """
    Check that the PDF covers the requested date:
      - the header 'From date' and 'To date' must equal the requested date
      - count data rows whose receipt date equals the requested date
    """
    res = {"header_from": "?", "header_to": "?", "rows_on_date": 0, "ok": False}
    if pypdf is None:
        res["note"] = "pypdf not installed - validation skipped"
        return res
    text = "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(path).pages)
    want = datetime.strptime(date_iso, "%Y-%m-%d").strftime("%d/%m/%Y")

    m = re.search(r"From date\s*(\d{2}/\d{2}/\d{4}).*?To date\s*(\d{2}/\d{2}/\d{4})", text, re.S)
    if m:
        res["header_from"], res["header_to"] = m.group(1), m.group(2)

    # every data row carries the receipt date; the header shows it twice (from/to)
    res["rows_on_date"] = max(0, text.count(want) - 2)
    res["ok"] = (res["header_from"] == want and res["header_to"] == want)
    return res


# ---------------------------------------------------------------- site access

class Site:
    def __init__(self, delay):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 (tg-claims-fetch; polite crawler)"
        self._districts = None
        self._ac_cache = {}
        # The site serves an incomplete TLS chain. Try strict verification
        # first, then fall back (same as 'curl -k').
        try:
            self.s.get(PAGE_URL, timeout=30)
        except requests.exceptions.SSLError:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            self.s.verify = False
            print("WARNING: the site certificate chain is invalid. "
                  "Continue without certificate verification (same as curl -k).",
                  file=sys.stderr)

    def tokens(self):
        r = self.s.get(PAGE_URL, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return hidden_fields(r.text)

    def post(self, data):
        time.sleep(self.delay)
        r = self.s.post(PAGE_URL, data=data, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r

    def districts(self):
        """All districts as [(value, label), ...]. Cached."""
        if self._districts is None:
            r = self.s.get(PAGE_URL, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            self._districts = [(v, lbl) for v, lbl in parse_select(r.text, "ddlDist")
                               if v != "0"]
        return self._districts

    def acs(self, dist_value):
        """All constituencies of one district as [(value, label), ...]. Cached."""
        if dist_value not in self._ac_cache:
            toks = self.tokens()
            data = dict(toks)
            data.update({
                PREFIX + "ddlDist": str(dist_value),
                PREFIX + "ddlAC": "0",
                PREFIX + "ddlformtype": "form9",
                PREFIX + "txt_date": "",
            })
            r = self.post(data)
            self._ac_cache[dist_value] = [(v, lbl) for v, lbl in
                                          parse_select(r.text, "ddlAC") if v != "0"]
        return self._ac_cache[dist_value]

    def fetch_pdf(self, dist_value, ac_value, form, date_iso):
        """
        Reproduce the browser flow:
          1. GET page            -> fresh hidden tokens
          2. POST district only  -> server fills the AC dropdown (refreshes tokens)
          3. POST full selection -> server stores the selection ('View PDF')
          4. GET PDFGeneration.aspx -> the PDF
        Returns (pdf_bytes, error_string).
        """
        toks = self.tokens()
        data1 = dict(toks)
        data1.update({
            PREFIX + "ddlDist": str(dist_value),
            PREFIX + "ddlAC": "0",
            PREFIX + "ddlformtype": "form9",
            PREFIX + "txt_date": "",
        })
        r1 = self.post(data1)

        toks2 = hidden_fields(r1.text)
        data2 = dict(toks2)
        data2.update({
            PREFIX + "ddlDist": str(dist_value),
            PREFIX + "ddlAC": str(ac_value),
            PREFIX + "ddlformtype": form,
            PREFIX + "txt_date": date_iso,
            PREFIX + "btnlogin": "View PDF",
        })
        r2 = self.post(data2)
        if "window.open" not in r2.text and "PDFGeneration" not in r2.text:
            m = re.search(r"alert\('([^']*)'\)", r2.text)
            return None, (m.group(1) if m else "no PDF trigger in the response")

        time.sleep(self.delay)
        r3 = self.s.get(PDF_URL, timeout=REQUEST_TIMEOUT)
        ctype = r3.headers.get("Content-Type", "").split(";")[0].strip()
        if ctype != "application/pdf":
            return None, "PDFGeneration.aspx returned %s (HTTP %s)" % (ctype, r3.status_code)
        return r3.content, ""


# ---------------------------------------------------------------- CLI

def ask_dates(args):
    """Read the date range from flags, or ask the user when not given."""
    if not args.dfrom:
        print("Give the date range for the download (ISO format: yyyy-mm-dd).")
        try:
            args.dfrom = input("From date: ").strip()
            args.dto = args.dto or input("To date   : ").strip() or args.dfrom
        except EOFError:
            sys.exit("ERROR: no --from date given and no interactive input available.")
    if not args.dto:
        args.dto = args.dfrom
    for d in (args.dfrom, args.dto):
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            sys.exit("ERROR: date '%s' is not in yyyy-mm-dd format." % d)
    if args.dto < args.dfrom:
        sys.exit("ERROR: the to-date is before the from-date.")


def resolve_targets(site, args):
    """
    Resolve --district / --ac into a list of (district, ac) pairs.
    No filter means everything: all districts and all constituencies.
    """
    districts = site.districts()

    if args.district:
        ds = match_options(districts, args.district, "district")
        if not ds:
            sys.exit("ERROR: district '%s' not found. Use --list to see all names." % args.district)
    else:
        ds = districts

    pairs = []
    if args.ac:
        for dval, dlbl in ds:
            hits = match_options(site.acs(dval), args.ac, "constituency")
            for aval, albl in hits:
                pairs.append((dval, dlbl, aval, albl))
        if not pairs:
            sys.exit("ERROR: constituency '%s' not found in the selected district(s)." % args.ac)
    else:
        for dval, dlbl in ds:
            for aval, albl in site.acs(dval):
                pairs.append((dval, dlbl, aval, albl))
    return pairs


def print_list(site):
    for dval, dlbl in site.districts():
        print("%s" % dlbl)
        for aval, albl in site.acs(dval):
            print("    %s" % albl)


def main():
    ap = argparse.ArgumentParser(
        description="Download Telangana CEO Claims & Objections PDFs for a date range.")
    ap.add_argument("--from", dest="dfrom", help="from date, yyyy-mm-dd")
    ap.add_argument("--to", dest="dto", help="to date, yyyy-mm-dd (default: same as from)")
    ap.add_argument("--district", help="district name or number (default: all districts)")
    ap.add_argument("--ac", help="assembly constituency name or number (default: all)")
    ap.add_argument("--form", help="form type(s), comma separated "
                                   "(default: all). Values: %s" % ", ".join(FORM_TYPES))
    ap.add_argument("--outdir", default="./claims_pdfs", help="output directory")
    ap.add_argument("--delay", type=float, default=3.0,
                    help="seconds between requests (default 3)")
    ap.add_argument("--dry-run", action="store_true", help="show the plan and exit")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--list", action="store_true", help="list all districts and constituencies")
    args = ap.parse_args()

    site = Site(args.delay)

    if args.list:
        print_list(site)
        return

    ask_dates(args)
    dates = list(date_range(args.dfrom, args.dto))

    forms = list(FORM_TYPES) if not args.form else \
        [f.strip().lower() for f in args.form.split(",") if f.strip()]
    for f in forms:
        if f not in FORM_TYPES:
            ap.error("unknown form type '%s'. Values: %s" % (f, ", ".join(FORM_TYPES)))

    pairs = resolve_targets(site, args)

    total = len(dates) * len(pairs) * len(forms)
    est_min = total * (3 * args.delay + 2) / 60.0
    print("\nPlan: %d date(s) x %d constituency(ies) x %d form type(s) = %d PDFs"
          % (len(dates), len(pairs), len(forms), total))
    print("Dates: %s to %s | Output: %s | Estimated time: %.0f min"
          % (args.dfrom, args.dto, args.outdir, est_min))
    if args.dry_run:
        return
    if total > 100 and not args.yes:
        try:
            answer = input("This is a large run. Continue? [y/N] ").strip().lower()
        except EOFError:
            answer = "n"
        if answer != "y":
            print("Stopped. Use --yes to skip this question.")
            return

    os.makedirs(args.outdir, exist_ok=True)
    manifest = os.path.join(args.outdir, "manifest.csv")
    done = 0
    with open(manifest, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "district", "ac", "form", "file",
                    "header_from", "header_to", "rows_on_date", "status"])
        for date_iso in dates:
            for dval, dlbl, aval, albl in pairs:
                for form in forms:
                    done += 1
                    name = "%s_AC%s_%s.pdf" % (form, aval, date_iso)
                    path = os.path.join(args.outdir, name)
                    print("[%d/%d] %s | %s | %s | %s ..."
                          % (done, total, dlbl, albl, FORM_TYPES[form], date_iso))
                    if os.path.exists(path):
                        print("  already downloaded, skip")
                        continue
                    try:
                        pdf, err = site.fetch_pdf(dval, aval, form, date_iso)
                    except Exception as e:
                        pdf, err = None, str(e)
                    if pdf is None:
                        status = ("NO DATA" if "No Record Found" in err
                                  else "FAILED: %s" % err)
                        print("  %s" % status)
                        w.writerow([date_iso, dlbl, albl, form, name,
                                    "", "", 0, status])
                        continue
                    with open(path, "wb") as f:
                        f.write(pdf)
                    v = validate_pdf(path, date_iso)
                    status = v.get("note", "OK" if v["ok"] else "DATE MISMATCH")
                    print("  saved %s (%d bytes), rows on date: %s, %s"
                          % (name, len(pdf), v["rows_on_date"], status))
                    w.writerow([date_iso, dlbl, albl, form, name,
                                v["header_from"], v["header_to"],
                                v["rows_on_date"], status])

    print("\nDone. Manifest: %s" % manifest)


if __name__ == "__main__":
    main()
