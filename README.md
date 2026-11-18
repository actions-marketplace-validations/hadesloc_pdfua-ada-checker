---
title: PDF UA & ADA Title II Compliance Checker
emoji: 🛡️
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: true
license: mit
short_description: Free ISO 14289-1 (PDF/UA-1), WCAG 2.1 AA & Section 508 PDF Validator (veraPDF 1.30)
---

# Free Online PDF/UA-1, ADA Title II & Section 508 PDF Accessibility Checker + GitHub Action

**Live Free Web Instances (No Account Required, Zero File Retention):**
- **Full veraPDF 1.30.3 Hosted Checker:** [https://pdfua-ada-checker.onrender.com](https://pdfua-ada-checker.onrender.com)
- **Instant Browser + veraPDF Checker Widget:** [https://hadesloc.github.io/pdfua-partner-desk/#free-checker](https://hadesloc.github.io/pdfua-partner-desk/#free-checker)
- **Official Mozilla-Verified Firefox Add-on (`v1.3.0`):** [addons.mozilla.org/addon/pdf-ua-ada-page-scanner](https://addons.mozilla.org/addon/pdf-ua-ada-page-scanner/)
- **2026 Empirical Benchmark (774 `.gov`/`.edu` PDFs audited):** [hadesloc.github.io/pdfua-partner-desk/benchmark-report-2026/](https://hadesloc.github.io/pdfua-partner-desk/benchmark-report-2026/)

Upload any PDF (up to **20 MB**) — or add `uses: hadesloc/pdfua-ada-checker@v1` to your GitHub Actions workflow — to run a real **veraPDF 1.30.3 (`--flavour ua1`)** ISO 14289-1:2014 machine validation combined with structural inspection for **U.S. DOJ 28 CFR Part 35 (ADA Title II)**, **WCAG 2.1 Level AA**, and **Section 508** compliance.

---

## Automate PDF/UA-1 & ADA Title II Checks in GitHub Actions (`@v1`)

Block inaccessible, untagged PDFs in Pull Requests before they reach production `.gov`, `.edu`, or documentation sites. Add `.github/workflows/pdf-accessibility.yml`:

```yaml
name: PDF Accessibility (PDF/UA-1 & ADA Title II)

on:
  pull_request:
    paths:
      - "**/*.pdf"
  workflow_dispatch:

jobs:
  pdfua-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Validate PDFs against ISO 14289-1 (PDF/UA-1)
        id: pdfua
        uses: hadesloc/pdfua-ada-checker@v1
        with:
          pdf-path: "."
          fail-on-violations: "true"
          max-files: "20"
```

### Action Inputs & Outputs

| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `pdf-path` | Input | `"."` | Directory, single `.pdf` file, or glob pattern (e.g. `public/pdfs/**/*.pdf`). |
| `api-url` | Input | `https://pdfua-ada-checker.onrender.com/api/check` | Zero-retention `veraPDF 1.30.3` validation endpoint (or self-hosted Docker URL). |
| `fail-on-violations` | Input | `"true"` | Fail the CI job (`exit 1`) when `failing-pdfs > 0`. Set `"false"` for report-only mode. |
| `max-files` | Input | `"20"` | Maximum PDFs audited per workflow run (up to 20 MB per PDF). |
| `total-pdfs` | Output | — | Number of PDFs audited. |
| `failing-pdfs` | Output | — | Number of PDFs with ISO 14289-1 rule failures. |
| `failed-rules` | Output | — | Total failed ISO 14289-1 rules across all audited PDFs. |

Each run writes a clause-by-clause Markdown report directly to **GitHub Actions Job Summary (`$GITHUB_STEP_SUMMARY`)**.

---

### What This Checker Inspects
- **ISO 14289-1 (PDF/UA-1) Clauses:** Full rule-by-rule `veraPDF 1.30.3` validation translated into plain-English screen-reader impact.
- **Document Structure & Metadata:** `/MarkInfo`, `/StructTreeRoot`, `pdfuaid:part=1`, `/Lang`, `/Title`, and `/DisplayDocTitle`.
- **Images, Tables, Links & AcroForms:** `<Figure>` `/Alt` coverage, `<TH>` `Scope`, hyperlink `/Contents`, fillable widget `/TU` tooltips, `/Tabs /S` focus order, embedded fonts (`CIDSet`), and scanned-image OCR detection.

### Strict Zero-Retention Privacy Policy
- Maximum file size: **20 MB**.
- Uploaded PDFs are validated inside an isolated ephemeral temporary directory and **permanently deleted immediately** before the HTTP response is returned (`0` files stored, `0` cloud retention).

### Local Desktop Studio `v1.6.1` — Free 5-Page Trial & Self-Serve Licenses (Offline, Zero Cloud Uploads)
- **Free 5-Page Windows Desktop Trial (`v1.6.1` — Auto-Fix, RapidOCR, AcroForms & Delivery ZIP Export):** [Download `PDFAccessibilityStudio-Setup-1.6.1.exe` on GitHub Releases](https://github.com/hadesloc/pdfua-partner-desk/releases/tag/v1.6.1)
- **Self-Serve Studio Licenses (50% Off w/ `EARLY50`):**
  - **Studio Solo (`$9.50` w/ `EARLY50` — 2 seats, unlimited pages):** [Checkout on Gumroad](https://pdfuapartnerdesk.gumroad.com/l/pdfua-express-5-pages/EARLY50?wanted=true)
  - **Studio Agency Batch (`$32.50` w/ `EARLY50` — 3 seats, batch CLI/autopilot):** [Checkout on Gumroad](https://pdfuapartnerdesk.gumroad.com/l/pdfua-remediation-12-pages/EARLY50?wanted=true)
  - **Studio Commercial (`$97.50` w/ `EARLY50` — 10 seats, full suite + PackProof):** [Checkout on Gumroad](https://pdfuapartnerdesk.gumroad.com/l/pdfua-remediation-30-pages/EARLY50?wanted=true)
- **Storefront, Free Online Tools & 2026 Benchmark Report:** [hadesloc.github.io/pdfua-partner-desk/](https://hadesloc.github.io/pdfua-partner-desk/)
- **Enterprise / Pilot Inquiries:** [Google Intake Form](https://docs.google.com/forms/d/e/1FAIpQLSfC50VFJdc55ug-sNpJON59JL9By6yIuRe8qysotQLX1cNtFA/viewform) · `drtheloc@gmail.com`

*Technical accessibility engineering, PDF/UA-1 software & standards validation — not legal advice.*
