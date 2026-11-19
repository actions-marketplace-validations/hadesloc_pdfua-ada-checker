from __future__ import annotations

from email.parser import BytesParser
from email.policy import default as email_default_policy
import base64
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any
import urllib.parse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

import pikepdf

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB strict cap
MAX_MULTIPART_OVERHEAD = 128 * 1024
MAX_PREVIEW_PAGES = 2  # Strict 2-page cap for free online structural preview

STOREFRONT_URL = "https://hadesloc.github.io/pdfua-partner-desk/"
GOOGLE_FORM_URL = (
    "https://docs.google.com/forms/d/e/1FAIpQLSfC50VFJdc55ug-sNpJON59JL9By6yIuRe8qysotQLX1cNtFA/viewform"
)
UPWORK_CATALOG_URL = "https://www.upwork.com/services/product/2120536307204819908"
CHECKOUT_SOLO_EARLY50_URL = "https://pdfuapartnerdesk.gumroad.com/l/pdfua-express-5-pages/EARLY50?wanted=true"
CHECKOUT_AGENCY_EARLY50_URL = "https://pdfuapartnerdesk.gumroad.com/l/pdfua-remediation-12-pages/EARLY50?wanted=true"
CHECKOUT_65_URL = CHECKOUT_AGENCY_EARLY50_URL
CHECKOUT_195_URL = "https://pdfuapartnerdesk.gumroad.com/l/pdfua-remediation-30-pages/EARLY50?wanted=true"
FIVERR_DFY_URL = "https://www.fiverr.com/pdfua_partner/remediate-pdf-documents-for-ada-title-ii-wcag-aa-and-pdf-ua-accessibility"
CONTACT_EMAIL = "drtheloc@gmail.com"

CLAUSE_GUIDE: dict[str, dict[str, str]] = {
    "5": {
        "title": "Missing PDF/UA-1 Conformance Metadata (pdfuaid:part=1)",
        "wcag": "ISO 14289-1 §5 · Compliance Gateway Identification",
        "impact": "Automated procurement portals, PAC 2026, and municipal accessibility audits reject the file as uncertified because its XMP metadata stream lacks the PDF/UA-1 conformance identifier.",
        "severity": "HIGH",
    },
    "6.1": {
        "title": "XMP Metadata Stream Out of Sync with Document Info",
        "wcag": "ISO 14289-1 §6.1 · Metadata Consistency",
        "impact": "Assistive technologies read conflicting or corrupted document metadata streams.",
        "severity": "MEDIUM",
    },
    "6.2": {
        "title": "PDF Is Not Marked as Tagged (/MarkInfo /Marked false)",
        "wcag": "WCAG 2.1 SC 1.3.1 Info and Relationships (Level A)",
        "impact": "Screen readers like NVDA and JAWS treat the entire document as an unstructured wall of text or announce 'Alert: Empty document'.",
        "severity": "CRITICAL",
    },
    "7.1": {
        "title": "Untagged Page Content, Unmarked Artifacts, or Missing Document Title",
        "wcag": "WCAG 2.1 SC 1.3.1, 1.3.2 & 2.4.2 Page Titled (Level A)",
        "impact": "Blind users hear running page headers/footers injected mid-sentence, miss untagged text/graphics, or hear a cryptic filename instead of a human-readable document title.",
        "severity": "CRITICAL",
    },
    "7.2": {
        "title": "Missing Natural Language (/Lang) or Broken Structure Tree Hierarchy",
        "wcag": "WCAG 2.1 SC 3.1.1 Language of Page & 1.3.1 Structure (Level A)",
        "impact": "Text-to-speech synthesizers mispronounce every paragraph using the wrong phonetic engine, or skip malformed list/table tags (<LI>/<LBody>, <TR>/<TD>).",
        "severity": "HIGH",
    },
    "7.3": {
        "title": "Figures & Graphics Missing Alternative Text (/Alt)",
        "wcag": "WCAG 2.1 SC 1.1.1 Non-text Content (Level A)",
        "impact": "Charts, seals, maps, signatures, and diagrams are either completely invisible to blind readers or announced only as 'Graphic' with zero context.",
        "severity": "CRITICAL",
    },
    "7.4": {
        "title": "Skipped or Illogical Heading Hierarchy (<H1>–<H6>)",
        "wcag": "WCAG 2.1 SC 1.3.1 & 2.4.6 Headings and Labels (Level AA)",
        "impact": "Screen-reader users navigating by the H-key shortcut lose document outline orientation when headings skip levels (e.g., jumping from H1 straight to H4).",
        "severity": "HIGH",
    },
    "7.5": {
        "title": "Data Tables Missing Header Scopes (<TH Scope>) or Regular Grid Structure",
        "wcag": "WCAG 2.1 SC 1.3.1 Info and Relationships (Level A)",
        "impact": "Users reading municipal budgets, fee schedules, or academic tables hear raw numbers without knowing which row or column header each cell belongs to.",
        "severity": "CRITICAL",
    },
    "7.15": {
        "title": "Mathematical Formulas Missing Spoken Alternative Description",
        "wcag": "WCAG 2.1 SC 1.1.1 Non-text Content (Level A)",
        "impact": "Equations and technical symbols are skipped or read as garbled characters.",
        "severity": "HIGH",
    },
    "7.18.1": {
        "title": "Interactive Annotations Missing Accessible Labels (/Contents)",
        "wcag": "WCAG 2.1 SC 4.1.2 Name, Role, Value (Level A)",
        "impact": "Keyboard and screen-reader users focus interactive page annotations without hearing what they do.",
        "severity": "HIGH",
    },
    "7.18.3": {
        "title": "Keyboard Tab Order Not Bound to Logical Structure (/Tabs /S)",
        "wcag": "WCAG 2.1 SC 2.4.3 Focus Order (Level A)",
        "impact": "Pressing Tab jumps erratically across X/Y page coordinates instead of following the logical reading order of links and form fields.",
        "severity": "HIGH",
    },
    "7.18.4": {
        "title": "Fillable Form Fields Not Tagged Inside <Form> / Missing /TU Tooltips",
        "wcag": "WCAG 2.1 SC 1.3.1, 3.3.2 & 4.1.2 Form Accessibility (Level A/AA)",
        "impact": "Citizens or students using screen readers cannot tell what information to type into permit, HR, or enrollment form fields.",
        "severity": "CRITICAL",
    },
    "7.18.5": {
        "title": "Hyperlinks Missing Accessible Link Descriptions or <Link> /OBJR Binding",
        "wcag": "WCAG 2.1 SC 2.4.4 Link Purpose in Context (Level A)",
        "impact": "Screen readers spell out 120-character raw URLs character-by-character or skip clickable links completely.",
        "severity": "HIGH",
    },
    "7.21": {
        "title": "Unembedded Fonts, Missing Unicode Map (/ToUnicode), or Invalid CIDSet",
        "wcag": "ISO 14289-1 §7.21 · Font & Character Encoding Integrity",
        "impact": "PAC 2026 and veraPDF fail strict PDF/UA-1 certification; missing /ToUnicode maps cause screen readers to read gibberish symbols instead of words.",
        "severity": "MEDIUM",
    },
}


def _lookup_clause_info(clause: str) -> dict[str, str]:
    c = (clause or "").strip()
    if c in CLAUSE_GUIDE:
        return CLAUSE_GUIDE[c]
    parts = c.split(".")
    for length in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:length])
        if prefix in CLAUSE_GUIDE:
            return CLAUSE_GUIDE[prefix]
    return {
        "title": f"ISO 14289-1 Clause {c or 'Structural'} Conformance Failure",
        "wcag": "ISO 14289-1 (PDF/UA-1) & WCAG 2.1 Level AA",
        "impact": "Fails strict PDF/UA-1 and PAC 2026 machine validation gates required by government and institutional accessibility standards.",
        "severity": "MEDIUM",
    }


def _discover_verapdf() -> tuple[str, Path] | None:
    env_jar = os.environ.get("VERAPDF_JAR", "").strip()
    jar_candidates: list[Path] = []
    if env_jar:
        jar_candidates.append(Path(env_jar))
    jar_candidates.append(Path("/opt/verapdf/cli-1.30.3.jar"))
    local_root = Path(__file__).resolve().parents[2]
    jar_candidates.append(local_root / "tools" / "verapdf" / "bin" / "cli-1.30.3.jar")

    jar_path = next((p for p in jar_candidates if p.is_file()), None)
    if jar_path is None:
        return None

    java_bin = shutil.which("java")
    if not java_bin:
        tools_dir = local_root / "tools"
        if tools_dir.is_dir():
            exe_name = "java.exe" if os.name == "nt" else "java"
            found = sorted(tools_dir.rglob(exe_name))
            if found:
                java_bin = str(found[0])
    if not java_bin:
        return None
    return java_bin, jar_path


def _run_verapdf(pdf_path: Path) -> dict[str, Any]:
    discovered = _discover_verapdf()
    if discovered is None:
        return {"available": False, "status": "UNAVAILABLE", "findings": []}
    java_bin, jar_path = discovered
    cmd = [
        java_bin,
        "-Xmx512m",
        "-Dfile.encoding=UTF8",
        "-XX:+IgnoreUnrecognizedVMOptions",
        "--add-exports=java.base/sun.security.pkcs=ALL-UNNAMED",
        "-cp",
        str(jar_path),
        "org.verapdf.apps.GreenfieldCliWrapper",
        "--flavour",
        "ua1",
        "--format",
        "mrr",
        str(pdf_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=50,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"available": True, "status": "TIMEOUT", "findings": []}

    raw_xml = proc.stdout.decode("utf-8", errors="replace")
    idx = raw_xml.find("<report")
    if idx < 0:
        return {"available": True, "status": "ERROR", "findings": []}
    try:
        root = ET.fromstring(raw_xml[idx:])
    except ET.ParseError:
        return {"available": True, "status": "ERROR", "findings": []}

    val_rep = root.find(".//validationReport")
    if val_rep is None:
        return {"available": True, "status": "ENCRYPTED_OR_UNPARSEABLE", "findings": []}

    is_compliant = val_rep.attrib.get("isCompliant", "false").lower() == "true"
    details = val_rep.find("details")
    failed_rules = int(details.attrib.get("failedRules", "0")) if details is not None else 0
    passed_rules = int(details.attrib.get("passedRules", "0")) if details is not None else 0
    failed_checks = int(details.attrib.get("failedChecks", "0")) if details is not None else 0
    passed_checks = int(details.attrib.get("passedChecks", "0")) if details is not None else 0

    by_clause: dict[str, dict[str, Any]] = {}
    if details is not None:
        for rule in details.findall("rule"):
            if rule.attrib.get("status", "").lower() != "failed":
                continue
            clause = rule.attrib.get("clause", "").strip() or "Other"
            r_checks = int(rule.attrib.get("failedChecks", "1") or "1")
            desc_el = rule.find("description")
            raw_desc = " ".join((desc_el.text or "").split()) if desc_el is not None else ""
            meta = _lookup_clause_info(clause)
            bucket = by_clause.setdefault(
                clause,
                {
                    "clause": clause,
                    "title": meta["title"],
                    "wcag": meta["wcag"],
                    "impact": meta["impact"],
                    "severity": meta["severity"],
                    "failed_rules": 0,
                    "failed_checks": 0,
                    "spec_description": raw_desc,
                },
            )
            bucket["failed_rules"] += 1
            bucket["failed_checks"] += r_checks
            if raw_desc and not bucket["spec_description"]:
                bucket["spec_description"] = raw_desc

    sev_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
    findings = sorted(
        by_clause.values(),
        key=lambda x: (sev_rank.get(x["severity"], 3), -x["failed_checks"], x["clause"]),
    )
    return {
        "available": True,
        "status": "COMPLETED",
        "engine": "veraPDF 1.30.3 (ISO 14289-1:2014 PDF/UA-1)",
        "is_compliant": is_compliant and failed_rules == 0,
        "failed_rules": failed_rules,
        "passed_rules": passed_rules,
        "failed_checks": failed_checks,
        "passed_checks": passed_checks,
        "findings": findings,
    }


def _inspect_structure_and_quote(pdf_path: Path, verapdf_res: dict[str, Any]) -> dict[str, Any]:
    with pikepdf.Pdf.open(pdf_path) as pdf:
        pages = len(pdf.pages)
        root = pdf.Root
        mark_info = root.get("/MarkInfo")
        marked = bool(isinstance(mark_info, pikepdf.Dictionary) and bool(mark_info.get("/Marked", False)))
        sroot = root.get("/StructTreeRoot")
        has_struct_tree = isinstance(sroot, pikepdf.Dictionary)
        is_tagged = marked and has_struct_tree

        lang_val = str(root.get("/Lang", "")).strip() if "/Lang" in root else ""
        vp = root.get("/ViewerPreferences")
        display_doc_title = bool(
            isinstance(vp, pikepdf.Dictionary) and bool(vp.get("/DisplayDocTitle", False))
        )

        xmp_bytes = b""
        if "/Metadata" in root:
            try:
                xmp_bytes = bytes(root.Metadata.read_bytes())
            except Exception:
                xmp_bytes = b""
        xmp_text = xmp_bytes.decode("utf-8", errors="ignore")
        has_pdfua_id = "pdfuaid:part" in xmp_text
        has_xmp_title = ("dc:title" in xmp_text) and bool(re.search(r"<rdf:li[^>]*>[^<\s]", xmp_text))
        info_title = ""
        if pdf.docinfo and "/Title" in pdf.docinfo:
            try:
                info_title = str(pdf.docinfo["/Title"]).strip()
            except Exception:
                info_title = ""
        has_title = has_xmp_title or bool(info_title)

        acro = root.get("/AcroForm")
        form_fields_total = 0
        form_fields_missing_tu = 0
        is_signed = False
        if isinstance(acro, pikepdf.Dictionary) and "/Fields" in acro:
            seen_f: set[tuple[int, int]] = set()
            stack = list(acro.Fields)
            while stack:
                item = stack.pop()
                if not isinstance(item, pikepdf.Dictionary):
                    continue
                og = item.objgen
                if og != (0, 0):
                    if og in seen_f:
                        continue
                    seen_f.add(og)
                ft = str(item.get("/FT", ""))
                if ft == "/Sig" and "/V" in item:
                    is_signed = True
                if "/Kids" in item and "/T" not in item:
                    stack.extend(list(item.Kids))
                    continue
                if ft in ("/Tx", "/Btn", "/Ch") or ("/T" in item and ft != "/Sig"):
                    form_fields_total += 1
                    tu = str(item.get("/TU", "")).strip() if "/TU" in item else ""
                    if not tu:
                        form_fields_missing_tu += 1

        # Walk up to 2,500 structure nodes for fast table / figure / heading stats
        table_count = 0
        heading_count = 0
        figure_count = 0
        figures_missing_alt = 0
        if has_struct_tree:
            seen_s: set[tuple[int, int]] = set()
            s_stack: list[Any] = [sroot.get("/K")]
            visited = 0
            while s_stack and visited < 2500:
                curr = s_stack.pop()
                if isinstance(curr, pikepdf.Array):
                    s_stack.extend(list(curr))
                    continue
                if not isinstance(curr, pikepdf.Dictionary):
                    continue
                og = curr.objgen
                if og != (0, 0):
                    if og in seen_s:
                        continue
                    seen_s.add(og)
                visited += 1
                tag = str(curr.get("/S", "")).lstrip("/")
                if tag == "Table":
                    table_count += 1
                elif tag in ("H", "H1", "H2", "H3", "H4", "H5", "H6"):
                    heading_count += 1
                elif tag == "Figure":
                    figure_count += 1
                    alt = str(curr.get("/Alt", "")).strip() if "/Alt" in curr else ""
                    if not alt:
                        figures_missing_alt += 1
                if "/K" in curr:
                    s_stack.append(curr.get("/K"))

        # Check first up to 10 pages for scanned image-only heuristic & link annotations
        sample_pages = min(pages, 10)
        image_pages = 0
        textless_pages = 0
        links_missing_contents = 0
        for idx in range(sample_pages):
            pg = pdf.pages[idx]
            res = pg.get("/Resources")
            has_xobj_img = False
            if isinstance(res, pikepdf.Dictionary) and "/XObject" in res:
                xobj = res.get("/XObject")
                if isinstance(xobj, pikepdf.Dictionary):
                    for _, xo in xobj.items():
                        if isinstance(xo, pikepdf.Stream) and str(xo.get("/Subtype", "")) == "/Image":
                            has_xobj_img = True
                            break
            if has_xobj_img:
                image_pages += 1
            raw_stream = b""
            try:
                if "/Contents" in pg:
                    c_obj = pg.Contents
                    if isinstance(c_obj, pikepdf.Array):
                        for part in c_obj:
                            if isinstance(part, pikepdf.Stream):
                                raw_stream += bytes(part.read_bytes())[:8192]
                    elif isinstance(c_obj, pikepdf.Stream):
                        raw_stream = bytes(c_obj.read_bytes())[:16384]
            except Exception:
                raw_stream = b""
            if b"BT" not in raw_stream and b"Tj" not in raw_stream and b"TJ" not in raw_stream:
                textless_pages += 1

            if "/Annots" in pg and isinstance(pg.Annots, pikepdf.Array):
                for annot in pg.Annots:
                    if isinstance(annot, pikepdf.Dictionary) and str(annot.get("/Subtype", "")) == "/Link":
                        contents_val = str(annot.get("/Contents", "")).strip() if "/Contents" in annot else ""
                        if not contents_val:
                            links_missing_contents += 1

        likely_scanned_ocr = sample_pages > 0 and image_pages > 0 and textless_pages >= max(1, int(sample_pages * 0.7))

    has_forms = form_fields_total > 0
    has_tables = table_count > 0

    if has_forms:
        tier_code = "FILLABLE_ACROFORMS"
        tier_label = "Interactive Fillable AcroForms"
        wholesale_per_page = 6.50
        retail_per_page = 15.00
    elif likely_scanned_ocr:
        tier_code = "SCANNED_OCR"
        tier_label = "Scanned OCR + Searchable PDF/UA"
        wholesale_per_page = 3.50
        retail_per_page = 9.00
    elif has_tables:
        tier_code = "STRUCTURED_TABLES"
        tier_label = "Structured Data Tables & Reports"
        wholesale_per_page = 3.00
        retail_per_page = 8.00
    else:
        tier_code = "SIMPLE_TEXT"
        tier_label = "Born-Digital Text & Headings"
        wholesale_per_page = 2.00
        retail_per_page = 5.00

    raw_wholesale = round(pages * wholesale_per_page, 2)
    wholesale_total = max(35.00, raw_wholesale) if pages > 0 else 35.00
    retail_total = max(65.00, round(pages * retail_per_page, 2))
    agency_profit = round(max(0.0, retail_total - wholesale_total), 2)

    if not has_forms and not likely_scanned_ocr and not has_tables and pages <= 12:
        fixed_pkg_label = "Starter PDF/UA Package ($65 up to 12 pages)"
        fixed_pkg_usd = 65.00
    elif not has_forms and not likely_scanned_ocr and pages <= 30:
        fixed_pkg_label = "Standard ADA Title II Reports & Tables ($195 up to 30 pages)"
        fixed_pkg_usd = 195.00
    elif (likely_scanned_ocr and pages <= 35) or (has_forms and pages <= 25):
        fixed_pkg_label = "Complex OCR / Fillable AcroForm Package ($390)"
        fixed_pkg_usd = 390.00
    else:
        fixed_pkg_label = f"Custom Batch Rate (${wholesale_total:,.2f} Agency Wholesale)"
        fixed_pkg_usd = wholesale_total

    # Fallback plain-English findings if veraPDF was unavailable
    findings = list(verapdf_res.get("findings") or [])
    if not verapdf_res.get("available"):
        if not is_tagged:
            m = _lookup_clause_info("6.2")
            findings.append({"clause": "6.2", **m, "failed_rules": 1, "failed_checks": pages})
        if not has_pdfua_id:
            m = _lookup_clause_info("5")
            findings.append({"clause": "5", **m, "failed_rules": 1, "failed_checks": 1})
        if not has_title or not display_doc_title:
            m = _lookup_clause_info("7.1")
            findings.append({"clause": "7.1", **m, "failed_rules": 1, "failed_checks": 1})
        if not lang_val:
            m = _lookup_clause_info("7.2")
            findings.append({"clause": "7.2", **m, "failed_rules": 1, "failed_checks": 1})
        if figures_missing_alt > 0:
            m = _lookup_clause_info("7.3")
            findings.append({"clause": "7.3", **m, "failed_rules": 1, "failed_checks": figures_missing_alt})
        if form_fields_missing_tu > 0:
            m = _lookup_clause_info("7.18.4")
            findings.append({"clause": "7.18.4", **m, "failed_rules": 1, "failed_checks": form_fields_missing_tu})
        if links_missing_contents > 0:
            m = _lookup_clause_info("7.18.5")
            findings.append({"clause": "7.18.5", **m, "failed_rules": 1, "failed_checks": links_missing_contents})

    is_pass = (
        bool(verapdf_res.get("is_compliant"))
        if verapdf_res.get("status") == "COMPLETED"
        else (len(findings) == 0 and is_tagged and has_pdfua_id)
    )

    return {
        "structure": {
            "pages": pages,
            "is_tagged": is_tagged,
            "has_pdfua_id": has_pdfua_id,
            "language": lang_val or None,
            "has_title": has_title,
            "display_doc_title": display_doc_title,
            "table_count": table_count,
            "heading_count": heading_count,
            "figure_count": figure_count,
            "figures_missing_alt": figures_missing_alt,
            "form_fields_total": form_fields_total,
            "form_fields_missing_tu": form_fields_missing_tu,
            "links_missing_contents_sample": links_missing_contents,
            "likely_scanned_ocr": likely_scanned_ocr,
            "is_cryptographically_signed": is_signed,
        },
        "quote": {
            "tier_code": tier_code,
            "tier_label": tier_label,
            "pages": pages,
            "wholesale_per_page_usd": wholesale_per_page,
            "wholesale_total_usd": wholesale_total,
            "retail_per_page_usd": retail_per_page,
            "retail_total_usd": retail_total,
            "agency_net_margin_usd": agency_profit,
            "fixed_package_label": fixed_pkg_label,
            "fixed_package_usd": fixed_pkg_usd,
            "free_sample_eligible": pages <= 15,
        },
        "is_compliant": is_pass,
        "findings": findings,
    }


def analyze_pdf_bytes(pdf_bytes: bytes, filename: str = "document.pdf") -> dict[str, Any]:
    started = time.monotonic()
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("File exceeds the 20 MB maximum upload limit.")
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("Uploaded file is not a valid PDF (%PDF- header missing).")

    safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(filename).name).strip() or "document.pdf"
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"

    tmp_dir = Path(tempfile.mkdtemp(prefix="pdfua-ephemeral-"))
    tmp_pdf = tmp_dir / "input.pdf"
    ephemeral_deleted = False
    try:
        tmp_pdf.write_bytes(pdf_bytes)
        verapdf_res = _run_verapdf(tmp_pdf)
        enriched = _inspect_structure_and_quote(tmp_pdf, verapdf_res)
    except pikepdf.PasswordError as exc:
        raise ValueError("Password-protected PDF cannot be inspected without unlocking.") from exc
    except pikepdf.PdfError as exc:
        raise ValueError(f"Malformed or corrupted PDF file: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        ephemeral_deleted = not tmp_dir.exists()

    failed_rules = (
        verapdf_res.get("failed_rules", len(enriched["findings"]))
        if verapdf_res.get("status") == "COMPLETED"
        else len(enriched["findings"])
    )
    failed_checks = (
        verapdf_res.get("failed_checks", sum(f.get("failed_checks", 1) for f in enriched["findings"]))
        if verapdf_res.get("status") == "COMPLETED"
        else sum(f.get("failed_checks", 1) for f in enriched["findings"])
    )

    quote = enriched["quote"]
    pages = quote["pages"]
    clauses_list = ", ".join(f["clause"] for f in enriched["findings"][:6]) or "None"
    mail_subject = (
        f"Fix PDF/UA-1 Issues: {safe_name} ({pages} pages · {failed_rules} failed rules)"
    )
    mail_body = (
        f"Hi Loc,\n\n"
        f"I just ran \"{safe_name}\" ({pages} pages) through the Free PDF/UA & ADA Title II Checker:\n"
        f"- Status: {'PASS (0 failed rules)' if enriched['is_compliant'] else f'FAIL ({failed_rules} failed rules / {failed_checks} failed checks)'}\n"
        f"- Detected Complexity Tier: {quote['tier_label']}\n"
        f"- Violated ISO 14289-1 Clauses: {clauses_list}\n"
        f"- Quoted Partner Wholesale Cost: ${quote['wholesale_total_usd']:,.2f} (or Fixed Package: ${quote['fixed_package_usd']:,.2f})\n\n"
        f"Please let me know the turnaround to remediate this document (or run a free sample proof):\n"
    )
    mailto_url = (
        f"mailto:{urllib.parse.quote(CONTACT_EMAIL)}"
        f"?subject={urllib.parse.quote(mail_subject)}"
        f"&body={urllib.parse.quote(mail_body)}"
    )

    return {
        "filename": safe_name,
        "file_size_bytes": len(pdf_bytes),
        "verdict": "PASS_PDFUA1" if enriched["is_compliant"] else "ACTION_REQUIRED",
        "is_compliant": enriched["is_compliant"],
        "engine": verapdf_res.get("engine", "Structural PDF/UA-1 Inspector"),
        "verapdf_status": verapdf_res.get("status", "UNAVAILABLE"),
        "failed_rules": failed_rules,
        "passed_rules": verapdf_res.get("passed_rules", 0),
        "failed_checks": failed_checks,
        "passed_checks": verapdf_res.get("passed_checks", 0),
        "structure": enriched["structure"],
        "findings": enriched["findings"],
        "quote": quote,
        "cta": {
            "solo_early50_url": CHECKOUT_SOLO_EARLY50_URL,
            "agency_early50_url": CHECKOUT_AGENCY_EARLY50_URL,
            "fiverr_dfy_url": FIVERR_DFY_URL,
            "checkout_65_url": CHECKOUT_65_URL,
            "checkout_195_url": CHECKOUT_195_URL,
            "direct_checkout_url": (
                CHECKOUT_SOLO_EARLY50_URL
                if pages <= 5
                else (
                    CHECKOUT_AGENCY_EARLY50_URL
                    if quote["fixed_package_usd"] <= 65
                    else (CHECKOUT_195_URL if quote["fixed_package_usd"] <= 195 else FIVERR_DFY_URL)
                )
            ),
            "upwork_catalog_url": UPWORK_CATALOG_URL,
            "google_form_url": GOOGLE_FORM_URL,
            "storefront_url": STOREFRONT_URL,
            "mailto_url": mailto_url,
            "contact_email": CONTACT_EMAIL,
        },
        "privacy": {
            "max_upload_mb": 20,
            "ephemeral_deleted": ephemeral_deleted,
            "files_retained": 0,
        },
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def _build_preview_xmp(title: str) -> bytes:
    safe_title = xml_escape(title.strip() or "Accessible Document Preview")
    xmp = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '    <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        "      <dc:title>\n"
        "        <rdf:Alt>\n"
        f'          <rdf:li xml:lang="x-default">{safe_title}</rdf:li>\n'
        "        </rdf:Alt>\n"
        "      </dc:title>\n"
        "    </rdf:Description>\n"
        '    <rdf:Description rdf:about="" xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/">\n'
        "      <pdfuaid:part>1</pdfuaid:part>\n"
        "    </rdf:Description>\n"
        "  </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    )
    return xmp.encode("utf-8")


def _strip_cidset_from_resources(resources: Any, visited: set[tuple[int, int]]) -> int:
    removed = 0
    if not isinstance(resources, pikepdf.Dictionary):
        return 0
    og = resources.objgen
    if og != (0, 0):
        if og in visited:
            return 0
        visited.add(og)

    fonts = resources.get("/Font")
    if isinstance(fonts, pikepdf.Dictionary):
        for _, font_obj in list(fonts.items()):
            if not isinstance(font_obj, pikepdf.Dictionary):
                continue
            fd = font_obj.get("/FontDescriptor")
            if isinstance(fd, pikepdf.Dictionary) and "/CIDSet" in fd:
                del fd["/CIDSet"]
                removed += 1
            desc = font_obj.get("/DescendantFonts")
            if isinstance(desc, pikepdf.Array):
                for df in desc:
                    if isinstance(df, pikepdf.Dictionary):
                        dfd = df.get("/FontDescriptor")
                        if isinstance(dfd, pikepdf.Dictionary) and "/CIDSet" in dfd:
                            del dfd["/CIDSet"]
                            removed += 1

    xobjs = resources.get("/XObject")
    if isinstance(xobjs, pikepdf.Dictionary):
        for _, xo in list(xobjs.items()):
            if isinstance(xo, pikepdf.Stream) and str(xo.get("/Subtype", "")) == "/Form":
                sub_res = xo.get("/Resources")
                if sub_res is not None:
                    removed += _strip_cidset_from_resources(sub_res, visited)
    return removed


def _form_xobject_has_text(page: pikepdf.Page, xobj_name: Any) -> bool:
    try:
        res = page.get("/Resources")
        if not isinstance(res, pikepdf.Dictionary):
            return False
        xobjs = res.get("/XObject")
        if not isinstance(xobjs, pikepdf.Dictionary):
            return False
        xo = xobjs.get(xobj_name)
        if not isinstance(xo, pikepdf.Stream) or str(xo.get("/Subtype", "")) != "/Form":
            return False
        raw = bytes(xo.read_bytes())
        return (b"BT" in raw) and (b"Tj" in raw or b"TJ" in raw)
    except Exception:
        return False


def _is_struct_mc(op_name: str, operands: list[Any]) -> bool:
    if not operands:
        return False
    tag = str(operands[0])
    if tag == "/Artifact":
        return True
    if op_name == "BDC" and len(operands) >= 2:
        props = operands[1]
        if isinstance(props, pikepdf.Dictionary) and ("/MCID" in props or "/ActualText" in props):
            return True
    return False


_NON_TEXT_PAINT_OPS = {"Do", "S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "sh", "INLINE IMAGE"}
_TEXT_SHOW_OPS = {"Tj", "TJ", "'", '"'}


def _tag_untagged_page_stream(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    doc_elem: pikepdf.Dictionary,
) -> tuple[list[pikepdf.Dictionary], bool]:
    """Wrap unmarked BT..ET text blocks in /P MCIDs and graphics in /Artifact BMC..EMC.

    When constructing a fresh /StructTreeRoot (e.g., on an untagged PDF or a 2-page
    sliced preview), any stale pre-existing MCID BDC/EMC wrappers are stripped so
    newly assigned MCIDs map 1:1 to the new /StructTreeRoot.
    """
    ops = list(pikepdf.parse_content_stream(page))
    new_ops: list[tuple[list[Any], pikepdf.Operator]] = []
    # Track stack entries: "artifact", "oc", or "strip" (stale MCID wrapper removed)
    mc_stack: list[str] = []
    in_bt = False
    wrapped_bt = False
    mcid_elems: list[pikepdf.Dictionary] = []
    changed = False

    def _add_p_elem(mcid: int) -> None:
        elem = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/StructElem"),
                S=pikepdf.Name("/P"),
                P=doc_elem,
                Pg=page.obj,
                K=mcid,
            )
        )
        mcid_elems.append(elem)
        doc_elem["/K"].append(elem)

    for operands, operator in ops:
        op_name = str(operator)
        in_artifact = "artifact" in mc_stack

        if op_name in ("BMC", "BDC"):
            tag = str(operands[0]) if operands else ""
            if tag == "/Artifact":
                mc_stack.append("artifact")
                new_ops.append((operands, operator))
            elif op_name == "BDC" and len(operands) >= 2 and isinstance(operands[1], pikepdf.Dictionary) and "/MCID" in operands[1]:
                mc_stack.append("strip")
                changed = True
            else:
                mc_stack.append("other")
                new_ops.append((operands, operator))
            continue
        if op_name == "EMC":
            kind = mc_stack.pop() if mc_stack else "other"
            if kind != "strip":
                new_ops.append((operands, operator))
            continue

        if op_name == "BT":
            in_bt = True
            if not in_artifact:
                mcid = len(mcid_elems)
                _add_p_elem(mcid)
                new_ops.append(
                    ([pikepdf.Name("/P"), pikepdf.Dictionary(MCID=mcid)], pikepdf.Operator("BDC"))
                )
                wrapped_bt = True
                changed = True
            new_ops.append((operands, operator))
            continue

        if op_name == "ET":
            new_ops.append((operands, operator))
            in_bt = False
            if wrapped_bt:
                new_ops.append(([], pikepdf.Operator("EMC")))
                wrapped_bt = False
            continue

        if not in_bt and not in_artifact and op_name in _NON_TEXT_PAINT_OPS:
            if op_name == "Do" and operands and _form_xobject_has_text(page, operands[0]):
                mcid = len(mcid_elems)
                _add_p_elem(mcid)
                new_ops.append(
                    ([pikepdf.Name("/P"), pikepdf.Dictionary(MCID=mcid)], pikepdf.Operator("BDC"))
                )
                new_ops.append((operands, operator))
                new_ops.append(([], pikepdf.Operator("EMC")))
            else:
                new_ops.append(([pikepdf.Name("/Artifact")], pikepdf.Operator("BMC")))
                new_ops.append((operands, operator))
                new_ops.append(([], pikepdf.Operator("EMC")))
            changed = True
            continue

        new_ops.append((operands, operator))

    if wrapped_bt:
        new_ops.append(([], pikepdf.Operator("EMC")))

    if changed:
        page["/Contents"] = pdf.make_stream(pikepdf.unparse_content_stream(new_ops))
    return mcid_elems, changed


def _artifact_unmarked_ops_on_tagged_page(pdf: pikepdf.Pdf, page: pikepdf.Page) -> bool:
    """Wrap leftover unmarked painting/text ops on an already-tagged page as /Artifact."""
    ops = list(pikepdf.parse_content_stream(page))
    new_ops: list[tuple[list[Any], pikepdf.Operator]] = []
    mc_stack: list[bool] = []
    in_bt = False
    changed = False

    for operands, operator in ops:
        op_name = str(operator)
        in_struct_mc = any(mc_stack)

        if op_name in ("BMC", "BDC"):
            mc_stack.append(_is_struct_mc(op_name, operands))
            new_ops.append((operands, operator))
            continue
        if op_name == "EMC":
            if mc_stack:
                mc_stack.pop()
            new_ops.append((operands, operator))
            continue
        if op_name == "BT":
            in_bt = True
            new_ops.append((operands, operator))
            continue
        if op_name == "ET":
            in_bt = False
            new_ops.append((operands, operator))
            continue

        if not in_struct_mc:
            if (not in_bt and op_name in _NON_TEXT_PAINT_OPS) or (in_bt and op_name in _TEXT_SHOW_OPS):
                new_ops.append(([pikepdf.Name("/Artifact")], pikepdf.Operator("BMC")))
                new_ops.append((operands, operator))
                new_ops.append(([], pikepdf.Operator("EMC")))
                changed = True
                continue

        new_ops.append((operands, operator))

    if changed:
        page["/Contents"] = pdf.make_stream(pikepdf.unparse_content_stream(new_ops))
    return changed


def _apply_preview_structural_fixes(in_path: Path, out_path: Path, safe_name: str) -> list[str]:
    """Self-contained 2-page pikepdf structural preview fixer (IP-safe public preview)."""
    fixes: list[str] = []
    with pikepdf.Pdf.open(in_path) as pdf:
        root = pdf.Root

        # 1. Natural language (/Lang)
        existing_lang = str(root.get("/Lang", "")).strip() if "/Lang" in root else ""
        if not existing_lang:
            root["/Lang"] = pikepdf.String("en-US")
            fixes.append("Set document language (/Lang en-US)")

        # 2. ViewerPreferences /DisplayDocTitle
        vp = root.get("/ViewerPreferences")
        if not isinstance(vp, pikepdf.Dictionary):
            vp = pikepdf.Dictionary()
            root["/ViewerPreferences"] = vp
        if not bool(vp.get("/DisplayDocTitle", False)):
            vp["/DisplayDocTitle"] = True
            fixes.append("Enabled /ViewerPreferences /DisplayDocTitle")

        # 3. Document title + synced XMP (dc:title + pdfuaid:part=1)
        doc_title = ""
        if pdf.docinfo and "/Title" in pdf.docinfo:
            try:
                doc_title = str(pdf.docinfo["/Title"]).strip()
            except Exception:
                doc_title = ""
        if not doc_title:
            stem = re.sub(r"[_-]+", " ", Path(safe_name).stem).strip()
            doc_title = stem.title() if stem else "Accessible Document Preview"
        pdf.docinfo["/Title"] = doc_title
        meta_stream = pdf.make_stream(_build_preview_xmp(doc_title))
        meta_stream["/Type"] = pikepdf.Name("/Metadata")
        meta_stream["/Subtype"] = pikepdf.Name("/XML")
        root["/Metadata"] = meta_stream
        fixes.append("Synced XMP dc:title and PDF/UA-1 pdfuaid:part=1 metadata")

        # 4. MarkInfo /Marked true
        mark_info = root.get("/MarkInfo")
        if not isinstance(mark_info, pikepdf.Dictionary):
            mark_info = pikepdf.Dictionary()
            root["/MarkInfo"] = mark_info
        if not bool(mark_info.get("/Marked", False)):
            mark_info["/Marked"] = True
            fixes.append("Marked catalog as tagged (/MarkInfo /Marked true)")

        # 5. Remove broken/partial /CIDSet streams from font descriptors
        cidset_removed = 0
        visited_res: set[tuple[int, int]] = set()
        for page in pdf.pages:
            res = page.get("/Resources")
            if res is not None:
                cidset_removed += _strip_cidset_from_resources(res, visited_res)
        if cidset_removed > 0:
            fixes.append(f"Removed {cidset_removed} problematic /CIDSet font stream(s)")

        # 6. Page /Tabs /S and missing Link annotation /Contents
        tabs_fixed = 0
        links_fixed = 0
        for page in pdf.pages:
            if str(page.get("/Tabs", "")) != "/S":
                page["/Tabs"] = pikepdf.Name("/S")
                tabs_fixed += 1
            annots = page.get("/Annots")
            if isinstance(annots, pikepdf.Array):
                for annot in annots:
                    if not isinstance(annot, pikepdf.Dictionary):
                        continue
                    if str(annot.get("/Subtype", "")) == "/Link":
                        contents_val = str(annot.get("/Contents", "")).strip() if "/Contents" in annot else ""
                        if not contents_val:
                            uri_text = ""
                            act = annot.get("/A")
                            if isinstance(act, pikepdf.Dictionary) and "/URI" in act:
                                try:
                                    uri_text = str(act["/URI"]).strip()
                                except Exception:
                                    uri_text = ""
                            label = f"Link: {uri_text}" if uri_text else "Document hyperlink"
                            annot["/Contents"] = pikepdf.String(label[:180])
                            links_fixed += 1
        if tabs_fixed > 0:
            fixes.append(f"Set logical keyboard tab order (/Tabs /S) on {tabs_fixed} page(s)")
        if links_fixed > 0:
            fixes.append(f"Filled missing /Contents labels on {links_fixed} link annotation(s)")

        # 7. Structure Tree + Unmarked Content / Artifact wrapping
        sroot = root.get("/StructTreeRoot")
        has_existing_struct = isinstance(sroot, pikepdf.Dictionary) and "/K" in sroot

        if not has_existing_struct:
            sroot = pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name("/StructTreeRoot"),
                )
            )
            doc_elem = pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name("/StructElem"),
                    S=pikepdf.Name("/Document"),
                    P=sroot,
                    K=pikepdf.Array(),
                )
            )
            sroot["/K"] = doc_elem
            root["/StructTreeRoot"] = sroot

            parent_nums = pikepdf.Array()
            next_key = 0
            tagged_pages = 0
            link_objr_count = 0

            for pg_idx, page in enumerate(pdf.pages):
                mcid_elems, stream_changed = _tag_untagged_page_stream(pdf, page, doc_elem)
                if stream_changed:
                    tagged_pages += 1
                page["/StructParents"] = pg_idx
                parent_nums.append(pg_idx)
                parent_nums.append(pikepdf.Array(mcid_elems))
                next_key = pg_idx + 1

            # Bind Link annotations into <Link> /OBJR elements
            for page in pdf.pages:
                annots = page.get("/Annots")
                if not isinstance(annots, pikepdf.Array):
                    continue
                for annot in annots:
                    if not isinstance(annot, pikepdf.Dictionary):
                        continue
                    if str(annot.get("/Subtype", "")) != "/Link":
                        continue
                    annot_indirect = annot if annot.is_indirect else pdf.make_indirect(annot)
                    link_elem = pdf.make_indirect(
                        pikepdf.Dictionary(
                            Type=pikepdf.Name("/StructElem"),
                            S=pikepdf.Name("/Link"),
                            P=doc_elem,
                            Pg=page.obj,
                            K=pikepdf.Dictionary(
                                Type=pikepdf.Name("/OBJR"),
                                Pg=page.obj,
                                Obj=annot_indirect,
                            ),
                        )
                    )
                    doc_elem["/K"].append(link_elem)
                    annot_indirect["/StructParent"] = next_key
                    parent_nums.append(next_key)
                    parent_nums.append(link_elem)
                    next_key += 1
                    link_objr_count += 1

            sroot["/ParentTree"] = pdf.make_indirect(pikepdf.Dictionary(Nums=parent_nums))
            sroot["/ParentTreeNextKey"] = next_key
            fixes.append(
                f"Built /StructTreeRoot & tagged page text/artifacts across {max(1, tagged_pages)} page(s)"
            )
            if link_objr_count > 0:
                fixes.append(f"Bound {link_objr_count} link annotation(s) to <Link> /OBJR tags")
        else:
            # Already partially tagged: preserve tree, fill missing <Figure> /Alt, artifact unmarked ops
            fig_alt_fixed = 0
            seen_nodes: set[tuple[int, int]] = set()
            stack: list[Any] = [sroot.get("/K")]
            visited_nodes = 0
            while stack and visited_nodes < 2500:
                curr = stack.pop()
                if isinstance(curr, pikepdf.Array):
                    stack.extend(list(curr))
                    continue
                if not isinstance(curr, pikepdf.Dictionary):
                    continue
                og = curr.objgen
                if og != (0, 0):
                    if og in seen_nodes:
                        continue
                    seen_nodes.add(og)
                visited_nodes += 1
                if str(curr.get("/S", "")) == "/Figure":
                    alt_val = str(curr.get("/Alt", "")).strip() if "/Alt" in curr else ""
                    if not alt_val:
                        curr["/Alt"] = pikepdf.String("Document graphic (preview auto-tag)")
                        fig_alt_fixed += 1
                if "/K" in curr:
                    stack.append(curr.get("/K"))

            if fig_alt_fixed > 0:
                fixes.append(f"Added preview /Alt descriptions on {fig_alt_fixed} untagged <Figure> node(s)")

            wrapped_pages = 0
            for page in pdf.pages:
                if _artifact_unmarked_ops_on_tagged_page(pdf, page):
                    wrapped_pages += 1
            if wrapped_pages > 0:
                fixes.append(f"Wrapped unmarked graphics/content as /Artifact on {wrapped_pages} page(s)")

        pdf.save(out_path)
    return fixes


def preview_fix_pdf_bytes(pdf_bytes: bytes, filename: str = "document.pdf") -> dict[str, Any]:
    """Run a zero-retention, strictly 2-page PDF/UA-1 structural preview fix + veraPDF proof."""
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("File exceeds the 20 MB maximum upload limit.")
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("Uploaded file is not a valid PDF (%PDF- header missing).")

    safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(filename).name).strip() or "document.pdf"
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    stem = re.sub(r"\.pdf$", "", safe_name, flags=re.IGNORECASE) or "document"
    download_filename = f"{stem}-2p-preview-accessible.pdf"

    tmp_dir = Path(tempfile.mkdtemp(prefix="pdfua-preview-fix-"))
    src_path = tmp_dir / "source.pdf"
    sliced_path = tmp_dir / "preview_before.pdf"
    fixed_path = tmp_dir / "preview_after.pdf"
    ephemeral_deleted = False

    try:
        src_path.write_bytes(pdf_bytes)
        with pikepdf.Pdf.open(src_path) as src_pdf:
            total_source_pages = len(src_pdf.pages)
            if total_source_pages == 0:
                raise ValueError("Uploaded PDF contains zero pages.")
            pages_processed = min(total_source_pages, MAX_PREVIEW_PAGES)
            truncated_to_preview = total_source_pages > MAX_PREVIEW_PAGES

            if truncated_to_preview:
                sliced_pdf = pikepdf.Pdf.new()
                for idx in range(pages_processed):
                    sliced_pdf.pages.append(src_pdf.pages[idx])
                # Preserve catalog metadata attributes so before-validation reflects source accurately
                for key in ("/Lang", "/ViewerPreferences", "/MarkInfo"):
                    if key in src_pdf.Root:
                        try:
                            sliced_pdf.Root[key] = sliced_pdf.copy_foreign(src_pdf.Root[key])
                        except Exception:
                            pass
                if "/Metadata" in src_pdf.Root:
                    try:
                        raw_xmp = bytes(src_pdf.Root.Metadata.read_bytes())
                        ms = sliced_pdf.make_stream(raw_xmp)
                        ms["/Type"] = pikepdf.Name("/Metadata")
                        ms["/Subtype"] = pikepdf.Name("/XML")
                        sliced_pdf.Root["/Metadata"] = ms
                    except Exception:
                        pass
                if src_pdf.docinfo and "/Title" in src_pdf.docinfo:
                    try:
                        sliced_pdf.docinfo["/Title"] = str(src_pdf.docinfo["/Title"])
                    except Exception:
                        pass
                sliced_pdf.save(sliced_path)
            else:
                shutil.copyfile(src_path, sliced_path)

        before_vp = _run_verapdf(sliced_path)
        before_struct = _inspect_structure_and_quote(sliced_path, before_vp)
        fixes_applied = _apply_preview_structural_fixes(sliced_path, fixed_path, safe_name)
        after_vp = _run_verapdf(fixed_path)
        after_struct = _inspect_structure_and_quote(fixed_path, after_vp)
        fixed_bytes = fixed_path.read_bytes()
    except pikepdf.PasswordError as exc:
        raise ValueError("Password-protected PDF cannot be preview-fixed without unlocking.") from exc
    except pikepdf.PdfError as exc:
        raise ValueError(f"Malformed or corrupted PDF file: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        ephemeral_deleted = not tmp_dir.exists()

    verapdf_ready = before_vp.get("status") == "COMPLETED" and after_vp.get("status") == "COMPLETED"
    before_failed_rules = (
        int(before_vp.get("failed_rules", 0))
        if before_vp.get("status") == "COMPLETED"
        else len(before_struct["findings"])
    )
    after_failed_rules = (
        int(after_vp.get("failed_rules", 0))
        if after_vp.get("status") == "COMPLETED"
        else len(after_struct["findings"])
    )
    before_failed_checks = (
        int(before_vp.get("failed_checks", 0))
        if before_vp.get("status") == "COMPLETED"
        else sum(int(f.get("failed_checks", 1)) for f in before_struct["findings"])
    )
    after_failed_checks = (
        int(after_vp.get("failed_checks", 0))
        if after_vp.get("status") == "COMPLETED"
        else sum(int(f.get("failed_checks", 1)) for f in after_struct["findings"])
    )
    verapdf_compliant = (
        bool(after_vp.get("is_compliant", False))
        if after_vp.get("status") == "COMPLETED"
        else (after_failed_rules == 0)
    )

    mail_subject = (
        f"Full Remediation / Pilot Request: {safe_name} ({total_source_pages}p — 2p preview {before_failed_rules}->{after_failed_rules} rules)"
    )
    mail_body = (
        f"Hi Loc,\n\n"
        f"I just ran the Free 2-Page PDF/UA-1 Auto-Fix Preview on \"{safe_name}\" ({total_source_pages} total pages):\n"
        f"- 2-Page Preview Result: {before_failed_rules} -> {after_failed_rules} failed veraPDF rules ({before_failed_checks} -> {after_failed_checks} checks)\n"
        f"- Need full-document PDF/UA-1 + WCAG 2.1 AA remediation (all {total_source_pages} pages).\n\n"
        f"Please reply with turnaround or pilot pricing.\n"
    )
    mailto_url = (
        f"mailto:{urllib.parse.quote(CONTACT_EMAIL)}"
        f"?subject={urllib.parse.quote(mail_subject)}"
        f"&body={urllib.parse.quote(mail_body)}"
    )

    return {
        "ok": True,
        "verapdf_ready": verapdf_ready,
        "pages_processed": pages_processed,
        "total_source_pages": total_source_pages,
        "truncated_to_preview": truncated_to_preview,
        "before_failed_rules": before_failed_rules,
        "after_failed_rules": after_failed_rules,
        "before_failed_checks": before_failed_checks,
        "after_failed_checks": after_failed_checks,
        "verapdf_compliant": verapdf_compliant,
        "fixes_applied": fixes_applied,
        "files_retained": 0 if ephemeral_deleted else 1,
        "download_filename": download_filename,
        "preview_pdf_base64": base64.b64encode(fixed_bytes).decode("ascii"),
        "upgrade_ctas": {
            "solo_early50_url": CHECKOUT_SOLO_EARLY50_URL,
            "solo_price_usd": 9.50,
            "agency_early50_url": CHECKOUT_AGENCY_EARLY50_URL,
            "agency_price_usd": 32.50,
            "fiverr_dfy_url": FIVERR_DFY_URL,
            "fiverr_price_usd": 45.00,
            "contact_email": CONTACT_EMAIL,
            "mailto_url": mailto_url,
            "storefront_url": STOREFRONT_URL,
        },
    }


def _extract_upload(headers: Any, body: bytes) -> tuple[bytes, str]:
    ctype = (headers.get("Content-Type") or "").strip()
    if ctype.lower().startswith("application/pdf") or body.startswith(b"%PDF-"):
        fname = urllib.parse.unquote(headers.get("X-Filename") or "uploaded.pdf")
        return body, fname

    if "multipart/form-data" in ctype.lower():
        raw_msg = f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        msg = BytesParser(policy=email_default_policy).parsebytes(raw_msg)
        for part in msg.iter_parts():
            fname = part.get_filename()
            payload = part.get_payload(decode=True)
            if payload and (fname or payload.startswith(b"%PDF-")):
                return payload, fname or "uploaded.pdf"
    raise ValueError("No PDF file found in upload request.")


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Free Online PDF/UA-1, ADA Title II &amp; Section 508 Checker (veraPDF 1.30.3)</title>
<meta name="description" content="Instant free PDF accessibility validator powered by veraPDF 1.30.3 (ISO 14289-1 PDF/UA-1). Check ADA Title II, WCAG 2.1 AA, and Section 508 issues in plain English with zero file retention.">
<style>
  :root {
    --bg: #ffffff;
    --bg-subtle: #f8fafc;
    --bg-muted: #f1f5f9;
    --line: #e2e8f0;
    --line-strong: #cbd5e1;
    --ink: #0f172a;
    --ink-2: #334155;
    --muted: #475569;
    --brand: #1d4ed8;
    --brand-hover: #1e40af;
    --brand-soft: #eff6ff;
    --ok: #166534;
    --ok-bg: #f0fdf4;
    --ok-border: #bbf7d0;
    --err: #991b1b;
    --err-bg: #fef2f2;
    --err-border: #fecaca;
    --warn: #92400e;
    --warn-bg: #fffbeb;
    --warn-border: #fde68a;
    --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.05);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--ink);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }
  a { color: var(--brand); text-decoration: underline; text-underline-offset: 3px; }
  a:hover { color: var(--brand-hover); }
  a:focus-visible, button:focus-visible, [role="button"]:focus-visible {
    outline: 3px solid var(--brand);
    outline-offset: 2px;
  }
  .skip-link {
    position: absolute;
    left: -9999px;
    top: 8px;
    z-index: 9999;
    background: var(--ink);
    color: #ffffff;
    font-weight: 700;
    padding: 10px 16px;
    border-radius: 6px;
    text-decoration: none;
  }
  .skip-link:focus { left: 12px; }
  .site-header {
    background: var(--bg);
    border-bottom: 1px solid var(--line);
    position: sticky;
    top: 0;
    z-index: 40;
  }
  .header-inner {
    max-width: 1080px;
    margin: 0 auto;
    padding: 12px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
  }
  .brand {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    font-weight: 650;
    font-size: 1rem;
    color: var(--ink);
    text-decoration: none;
  }
  .brand:hover { color: var(--ink); text-decoration: none; }
  .brand-mark {
    width: 24px;
    height: 24px;
    border-radius: 6px;
    background: var(--brand);
    color: #ffffff;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .header-actions {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    align-items: center;
  }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 40px 24px 56px; }
  .eyebrow {
    display: inline-block;
    font-size: 0.84rem;
    font-weight: 600;
    color: var(--brand);
    margin-bottom: 8px;
  }
  .pill {
    display: inline-flex;
    align-items: center;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.74rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    text-transform: uppercase;
  }
  .pill-ok { background: var(--ok-bg); color: var(--ok); border: 1px solid var(--ok-border); }
  .pill-warn { background: var(--warn-bg); color: var(--warn); border: 1px solid var(--warn-border); }
  .pill-err { background: var(--err-bg); color: var(--err); border: 1px solid var(--err-border); }
  .pill-brand { background: var(--brand-soft); color: #1e3a8a; border: 1px solid #bfdbfe; }
  h1 {
    font-size: clamp(1.85rem, 3.2vw, 2.5rem);
    margin: 4px 0 12px;
    line-height: 1.15;
    letter-spacing: -0.025em;
    color: var(--ink);
  }
  .sublead { color: var(--ink-2); max-width: 740px; margin: 0 0 28px; font-size: 1.05rem; }
  .dropzone {
    background: var(--bg-subtle);
    border: 1.5px dashed #94a3b8;
    border-radius: 10px;
    padding: 36px 24px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.15s ease, background-color 0.15s ease;
  }
  .dropzone:hover { border-color: var(--brand); background: var(--brand-soft); }
  .dropzone.dragover { border-color: var(--ok); background: var(--ok-bg); }
  .chk-icon {
    width: 44px;
    height: 44px;
    margin: 0 auto 12px;
    border-radius: 8px;
    background: var(--brand-soft);
    color: var(--brand);
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .dropzone h2 { margin: 4px 0 6px; font-size: 1.15rem; color: var(--ink); letter-spacing: -0.015em; }
  .dropzone p { margin: 0 auto 18px; max-width: 580px; color: var(--muted); font-size: 0.9rem; }
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 40px;
    padding: 10px 18px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 0.9rem;
    border: 1px solid transparent;
    cursor: pointer;
    font-family: inherit;
    text-decoration: none;
    transition: background-color 0.15s ease, border-color 0.15s ease;
  }
  .btn:hover { text-decoration: none; }
  .btn-primary { background: var(--brand); border-color: var(--brand); color: #ffffff; }
  .btn-primary:hover { background: var(--brand-hover); border-color: var(--brand-hover); color: #ffffff; }
  .btn-sec { background: #ffffff; color: var(--ink); border-color: var(--line-strong); }
  .btn-sec:hover { background: var(--bg-subtle); border-color: #94a3b8; color: var(--ink); }
  .btn-sm { min-height: 36px; padding: 7px 13px; font-size: 0.82rem; }
  .privacy-strip {
    display: flex;
    gap: 20px;
    justify-content: center;
    flex-wrap: wrap;
    margin-top: 14px;
    font-size: 0.82rem;
    color: var(--muted);
  }
  .privacy-strip span { display: inline-flex; align-items: center; gap: 6px; }
  .privacy-strip span::before {
    content: "";
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--brand);
  }
  .card {
    background: #ffffff;
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 24px;
    margin-top: 24px;
    box-shadow: var(--shadow-sm);
  }
  .result-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; margin-top: 18px; }
  .kpi { background: var(--bg-subtle); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
  .kpi .lbl { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); font-weight: 600; }
  .kpi .val { font-size: 1.55rem; font-weight: 800; margin: 4px 0 2px; color: var(--ink); font-variant-numeric: tabular-nums; }
  .kpi .val.ok { color: var(--ok); }
  .kpi .val.err { color: var(--err); }
  .kpi .val.brand { color: var(--brand); }
  .kpi .hint { font-size: 0.82rem; color: var(--ink-2); }
  .cta-banner {
    background: var(--bg-subtle);
    border: 1px solid var(--line);
    border-left: 4px solid var(--brand);
    border-radius: 10px;
    padding: 24px;
    margin-top: 24px;
  }
  .cta-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }
  .mor-note { margin: 10px 0 0; font-size: 0.78rem; color: var(--muted); }
  .issue-item {
    background: var(--bg-subtle);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 16px 18px;
    margin-top: 12px;
  }
  .issue-item.issue-pass {
    background: var(--ok-bg);
    border-color: var(--ok-border);
  }
  .issue-head { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; align-items: center; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.82rem; color: var(--muted); }
  .checks-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin-top: 16px; }
  .chk {
    padding: 10px 12px;
    border-radius: 6px;
    background: #ffffff;
    border: 1px solid var(--line);
    font-size: 0.85rem;
    color: var(--ink-2);
  }
  .chk strong.ok { color: var(--ok); }
  .chk strong.err { color: var(--err); }
  #status-box {
    display: none;
    margin-top: 18px;
    padding: 14px 18px;
    border-radius: 8px;
    background: var(--brand-soft);
    border: 1px solid #bfdbfe;
    text-align: center;
    font-weight: 600;
    color: #1e3a8a;
  }
  #error-box {
    display: none;
    margin-top: 18px;
    background: var(--err-bg);
    border: 1px solid var(--err-border);
    color: var(--err);
    padding: 14px 18px;
    border-radius: 8px;
  }
  #results, #preview-results { display: none; }
  .mode-tabs {
    display: inline-flex;
    gap: 8px;
    padding: 5px;
    background: var(--bg-muted);
    border: 1px solid var(--line);
    border-radius: 10px;
    margin-bottom: 18px;
    flex-wrap: wrap;
  }
  .mode-tab {
    border: 1px solid transparent;
    background: transparent;
    color: var(--ink-2);
    font-family: inherit;
    font-size: 0.88rem;
    font-weight: 600;
    padding: 9px 16px;
    border-radius: 7px;
    cursor: pointer;
    transition: background-color 0.15s ease, color 0.15s ease;
  }
  .mode-tab:hover { color: var(--ink); }
  .mode-tab[aria-selected="true"] {
    background: #ffffff;
    color: var(--brand);
    border-color: var(--line);
    box-shadow: var(--shadow-sm);
  }
  .fixes-list {
    margin: 14px 0 0;
    padding-left: 20px;
    color: var(--ink-2);
    font-size: 0.92rem;
  }
  .fixes-list li { margin-bottom: 5px; }
  footer {
    margin-top: 48px;
    padding: 28px 0 12px;
    border-top: 1px solid var(--line);
    font-size: 0.84rem;
    color: var(--muted);
  }
  footer a { color: var(--ink-2); font-weight: 600; }
  @media (max-width: 640px) {
    .header-inner { padding: 10px 16px; }
    .wrap { padding: 28px 16px 40px; }
    .card, .cta-banner { padding: 18px; }
    .cta-row .btn { width: 100%; }
  }
</style>
</head>
<body>
<a class="skip-link" href="#main-content">Skip to main content</a>
<header class="site-header">
  <div class="header-inner">
    <a class="brand" href="https://hadesloc.github.io/pdfua-partner-desk/">
      <span class="brand-mark" aria-hidden="true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><path d="m9 15 2 2 4-4"></path></svg>
      </span>
      <span>PDF/UA Partner Desk</span>
    </a>
    <div class="header-actions">
      <span class="pill pill-brand">veraPDF 1.30.3 Engine</span>
      <a class="btn btn-sec btn-sm" href="https://hadesloc.github.io/pdfua-partner-desk/#calculator">ROI Estimator</a>
      <a class="btn btn-primary btn-sm" href="https://pdfuapartnerdesk.gumroad.com/l/pdfua-express-5-pages/EARLY50?wanted=true" target="_blank" rel="noopener">Studio Solo ($9.50)</a>
    </div>
  </div>
</header>

<main id="main-content" tabindex="-1" class="wrap">
  <span class="eyebrow">ISO 14289-1:2014 (PDF/UA-1) &middot; ADA Title II &middot; Section 508</span>
  <h1>Free Online PDF/UA-1 Checker &amp; 2-Page Auto-Fix Preview</h1>
  <p class="sublead">
    Validate any PDF against real <strong>veraPDF 1.30.3</strong> rules, or run our <strong>Free 2-Page Structural Auto-Fix Preview</strong> with live Before &rarr; After machine verification and 1-click PDF download. Zero file retention.
  </p>

  <div class="mode-tabs" role="tablist" aria-label="Choose inspection or preview repair mode">
    <button type="button" class="mode-tab" id="tab-preview-fix" role="tab" aria-selected="true" data-mode="preview-fix">Auto-Fix First 2 Pages Free (Live veraPDF Proof)</button>
    <button type="button" class="mode-tab" id="tab-audit" role="tab" aria-selected="false" data-mode="audit">Full Document Audit (veraPDF 1.30.3)</button>
  </div>

  <input type="file" id="file-input" accept="application/pdf,.pdf" aria-label="Select PDF file up to 20 megabytes" tabindex="-1" style="display:none">
  <div class="dropzone" id="dropzone" tabindex="0" role="button" aria-label="Upload PDF file up to 20 megabytes">
    <div class="chk-icon" aria-hidden="true">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
    </div>
    <h2 id="dz-title">Drop a PDF here to Auto-Fix the First 2 Pages Free</h2>
    <p id="dz-desc">Runs live Before &rarr; After veraPDF 1.30.3 validation, repairs PDF/UA-1 metadata, /Lang, /DisplayDocTitle, /MarkInfo, /Tabs /S, /CIDSet, link labels, and structure tags on up to 2 pages, and lets you download the fixed preview PDF immediately.</p>
    <div class="cta-row" style="justify-content:center;">
      <span class="btn btn-primary" id="dz-btn-label">Auto-Fix First 2 Pages Free (Max 20 MB)</span>
    </div>
  </div>
  <div class="privacy-strip">
    <span>Zero file retention &mdash; deleted immediately after scan</span>
    <span>Real veraPDF 1.30.3 Java engine</span>
    <span>Free &middot; No sign-up required</span>
  </div>

  <div id="status-box" role="status" aria-live="polite">Running veraPDF 1.30.3 ISO 14289-1 (PDF/UA-1) &amp; structural preview&hellip;</div>
  <div id="error-box" role="alert"></div>

  <div id="preview-results">
    <div class="card">
      <div class="result-head">
        <div>
          <span id="pf-verdict-pill" class="pill pill-ok">PREVIEW FIXED</span>
          <h2 id="pf-doc-title" style="margin:8px 0 2px; font-size:1.35rem;">preview-accessible.pdf</h2>
          <div id="pf-engine-line" class="mono"></div>
        </div>
        <button type="button" class="btn btn-primary" id="btn-download-preview-pdf">Download Fixed 2-Page Preview PDF</button>
      </div>

      <div class="kpi-grid">
        <div class="kpi">
          <div class="lbl">veraPDF Failed Rules</div>
          <div class="val ok" id="pf-kpi-rules">0 &rarr; 0</div>
          <div class="hint" id="pf-kpi-rules-hint">Live Before &rarr; After proof</div>
        </div>
        <div class="kpi">
          <div class="lbl">veraPDF Failed Checks</div>
          <div class="val ok" id="pf-kpi-checks">0 &rarr; 0</div>
          <div class="hint">ISO 14289-1 clause checks</div>
        </div>
        <div class="kpi">
          <div class="lbl">Preview Scope</div>
          <div class="val" id="pf-kpi-pages">2 / 2 Pages</div>
          <div class="hint" id="pf-kpi-trunc">Free 2-page preview cap</div>
        </div>
        <div class="kpi">
          <div class="lbl">Privacy Verification</div>
          <div class="val ok" style="font-size:1.3rem;">0 Retained</div>
          <div class="hint">Temp files deleted before response</div>
        </div>
      </div>

      <h3 style="margin:20px 0 6px; font-size:1.05rem;">Structural Repairs Applied (First &le;2 Pages)</h3>
      <ul id="pf-fixes-list" class="fixes-list"></ul>
    </div>

    <div class="cta-banner">
      <span class="pill pill-brand">Unlock Full-Document Unlimited Remediation</span>
      <h2 id="pf-cta-headline" style="margin:10px 0 6px; font-size:1.3rem;">Need all pages fixed (plus OCR, Tables, AcroForms &amp; Batch CLI)?</h2>
      <p style="margin:0; color:var(--ink-2); font-size:0.95rem;">
        Run <strong>PDF Accessibility Studio v1.6.1</strong> locally on Windows with zero page caps &mdash; <strong>50% off with EARLY50</strong> (Solo <strong>$9.50</strong> vs. $19 list &middot; Agency <strong>$32.50</strong> vs. $65 list &middot; Commercial <strong>$97.50</strong> vs. $195 list) &mdash; or hand the file off to our Done-For-You desk.
      </p>
      <div class="cta-row">
        <a id="pf-cta-solo" class="btn btn-primary" href="https://pdfuapartnerdesk.gumroad.com/l/pdfua-express-5-pages/EARLY50?wanted=true" target="_blank" rel="noopener">Studio Solo Offline ($9.50 w/ EARLY50)</a>
        <a id="pf-cta-agency" class="btn btn-sec" href="https://pdfuapartnerdesk.gumroad.com/l/pdfua-remediation-12-pages/EARLY50?wanted=true" target="_blank" rel="noopener">Studio Agency Batch ($32.50 w/ EARLY50)</a>
        <a id="pf-cta-fiverr" class="btn btn-sec" href="https://www.fiverr.com/pdfua_partner/remediate-pdf-documents-for-ada-title-ii-wcag-aa-and-pdf-ua-accessibility" target="_blank" rel="noopener">Done-For-You on Fiverr ($45 up to 15p)</a>
        <a id="pf-cta-mailto" class="btn btn-sec" href="mailto:drtheloc@gmail.com">Direct B2B Pilot (drtheloc@gmail.com)</a>
      </div>
      <p class="mor-note">Instant Gumroad license delivery (EARLY50 auto-applied) &middot; Fiverr Escrow available &middot; Zero cloud file retention</p>
    </div>
  </div>

  <div id="results">
    <div class="card">
      <div class="result-head">
        <div>
          <span id="verdict-pill" class="pill pill-err">NON-COMPLIANT</span>
          <h2 id="doc-title" style="margin:8px 0 2px; font-size:1.35rem;">document.pdf</h2>
          <div id="engine-line" class="mono"></div>
        </div>
        <div class="cta-row" style="margin-top:0;">
          <button type="button" class="btn btn-primary btn-sm" id="btn-run-preview-from-audit">Auto-Fix First 2 Pages Free</button>
          <button type="button" class="btn btn-sec btn-sm" id="btn-download-report">Download HTML Audit Report</button>
        </div>
      </div>

      <div class="kpi-grid">
        <div class="kpi">
          <div class="lbl">PDF/UA-1 Failed Rules</div>
          <div class="val" id="kpi-rules">0</div>
          <div class="hint" id="kpi-checks">0 failed clause checks</div>
        </div>
        <div class="kpi">
          <div class="lbl">Document Scope</div>
          <div class="val" id="kpi-pages">0 Pages</div>
          <div class="hint" id="kpi-tier">Born-Digital Text</div>
        </div>
        <div class="kpi">
          <div class="lbl">Studio License (EARLY50)</div>
          <div class="val brand" id="kpi-quote">$9.50</div>
          <div class="hint" id="kpi-wholesale">List $19 &middot; Agency $32.50 ($65 list)</div>
        </div>
        <div class="kpi">
          <div class="lbl">Privacy Verification</div>
          <div class="val ok" style="font-size:1.3rem;">0 Retained</div>
          <div class="hint">Ephemeral file purged after scan</div>
        </div>
      </div>

      <div class="checks-grid" id="struct-grid"></div>
    </div>

    <div class="cta-banner" id="cta-banner">
      <span class="pill pill-ok">Pixel-Identical Layout &middot; 0 Failed veraPDF 1.30.3 Rules</span>
      <h2 id="cta-headline" style="margin:10px 0 6px; font-size:1.3rem;">Need this PDF remediated to 0 failed PDF/UA-1 rules?</h2>
      <p id="cta-sub" style="margin:0; color:var(--ink-2); font-size:0.95rem;"></p>
      <div class="cta-row">
        <a id="cta-checkout" class="btn btn-primary" href="https://pdfuapartnerdesk.gumroad.com/l/pdfua-express-5-pages/EARLY50?wanted=true" target="_blank" rel="noopener">Studio Solo ($9.50 w/ EARLY50)</a>
        <a id="cta-agency" class="btn btn-sec" href="https://pdfuapartnerdesk.gumroad.com/l/pdfua-remediation-12-pages/EARLY50?wanted=true" target="_blank" rel="noopener">Studio Agency ($32.50 w/ EARLY50)</a>
        <a id="cta-fiverr" class="btn btn-sec" href="https://www.fiverr.com/pdfua_partner/remediate-pdf-documents-for-ada-title-ii-wcag-aa-and-pdf-ua-accessibility" target="_blank" rel="noopener">Done-For-You Fiverr ($45 / 15p)</a>
        <a id="cta-mailto" class="btn btn-sec" href="mailto:drtheloc@gmail.com">Direct B2B Pilot (drtheloc@gmail.com)</a>
      </div>
      <p class="mor-note">50% off EARLY50 auto-applied on Gumroad (Solo $9.50 / Agency $32.50 / Commercial $97.50) &middot; Fiverr Escrow ($45)</p>
    </div>

    <div class="card">
      <h2 style="margin-top:0; font-size:1.25rem;">Plain-English ISO 14289-1 &amp; WCAG 2.1 AA Diagnostic Findings</h2>
      <div id="issues-list"></div>
    </div>
  </div>

  <footer>
    <p>
      <strong>Privacy &amp; Technical Scope Notice:</strong> Uploaded files (max 20 MB) are processed inside an isolated ephemeral directory and permanently deleted before this page receives your report or 2-page preview PDF. Passing automated ISO 14289-1 (PDF/UA-1) rules in veraPDF 1.30.3 verifies machine structure, tags, metadata, and fonts; full WCAG 2.1 AA compliance also requires human review of alt-text accuracy and logical reading order in NVDA. Technical compliance engineering service &mdash; not legal advice.
    </p>
    <p>
      Operated by <strong>Loc Nguyen &mdash; PDF/UA Partner Desk</strong> &middot;
      <a href="https://hadesloc.github.io/pdfua-partner-desk/">Storefront &amp; Desktop Studio</a> &middot;
      <a href="mailto:drtheloc@gmail.com">drtheloc@gmail.com</a>
    </p>
  </footer>
</main>

<script>
(function () {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const statusBox = document.getElementById("status-box");
  const errorBox = document.getElementById("error-box");
  const resultsBox = document.getElementById("results");
  const previewBox = document.getElementById("preview-results");
  const tabPreview = document.getElementById("tab-preview-fix");
  const tabAudit = document.getElementById("tab-audit");
  const dzTitle = document.getElementById("dz-title");
  const dzDesc = document.getElementById("dz-desc");
  const dzBtnLabel = document.getElementById("dz-btn-label");

  let activeMode = "preview-fix";
  let lastFile = null;
  let lastReport = null;
  let lastPreview = null;

  const esc = (s) => String(s || "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
  const fmtUsd = (n) => "$" + Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function setMode(mode) {
    activeMode = mode;
    const isPreview = mode === "preview-fix";
    tabPreview.setAttribute("aria-selected", isPreview ? "true" : "false");
    tabAudit.setAttribute("aria-selected", isPreview ? "false" : "true");
    dzTitle.textContent = isPreview
      ? "Drop a PDF here to Auto-Fix the First 2 Pages Free"
      : "Drop a PDF here to run a full-document PDF/UA-1 audit";
    dzDesc.textContent = isPreview
      ? "Runs live Before -> After veraPDF 1.30.3 validation, repairs PDF/UA-1 metadata, /Lang, /DisplayDocTitle, /MarkInfo, /Tabs /S, /CIDSet, link labels, and structure tags on up to 2 pages, and lets you download the fixed preview PDF immediately."
      : "Evaluates ISO 14289-1 tags, reading order, figure alt text, table header scopes, AcroForm /TU tooltips, font encoding, and scanned text layers across all pages.";
    dzBtnLabel.textContent = isPreview
      ? "Auto-Fix First 2 Pages Free (Max 20 MB)"
      : "Select PDF to Validate (Max 20 MB)";
  }

  tabPreview.addEventListener("click", () => setMode("preview-fix"));
  tabAudit.addEventListener("click", () => setMode("audit"));

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("dragover"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleSelectedFile(e.dataTransfer.files[0]);
    }
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files[0]) handleSelectedFile(fileInput.files[0]);
  });

  function handleSelectedFile(file) {
    lastFile = file;
    if (activeMode === "preview-fix") {
      runPreviewFix(file);
    } else {
      runCheck(file);
    }
  }

  async function runPreviewFix(file) {
    errorBox.style.display = "none";
    previewBox.style.display = "none";
    if (file.size > 20 * 1024 * 1024) {
      errorBox.innerHTML = "<strong>File exceeds 20 MB limit.</strong> Email <a href='mailto:drtheloc@gmail.com'>drtheloc@gmail.com</a> for a complimentary B2B workstation pilot.";
      errorBox.style.display = "block";
      return;
    }
    statusBox.textContent = "Running veraPDF 1.30.3 Before check -> 2-page structural repair -> After veraPDF proof (~10-30s on cold start)...";
    statusBox.style.display = "block";
    try {
      const form = new FormData();
      form.append("file", file, file.name || "document.pdf");
      const resp = await fetch("./api/preview-fix", {
        method: "POST",
        headers: { "X-Filename": encodeURIComponent(file.name || "document.pdf") },
        body: form
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || ("Preview repair error (" + resp.status + ")"));
      }
      lastPreview = data;
      renderPreviewFix(data);
    } catch (err) {
      errorBox.textContent = "Error: " + (err && err.message ? err.message : String(err));
      errorBox.style.display = "block";
    } finally {
      statusBox.style.display = "none";
    }
  }

  function renderPreviewFix(d) {
    const pill = document.getElementById("pf-verdict-pill");
    if (d.verapdf_compliant) {
      pill.className = "pill pill-ok";
      pill.textContent = "PASS · 0 FAILED PDF/UA-1 RULES ON 2-PAGE PREVIEW";
    } else if (d.after_failed_rules < d.before_failed_rules) {
      pill.className = "pill pill-brand";
      pill.textContent = "IMPROVED · " + d.before_failed_rules + " -> " + d.after_failed_rules + " FAILED RULES";
    } else {
      pill.className = "pill pill-warn";
      pill.textContent = "PREVIEW PROCESSED · FULL REMEDIATION RECOMMENDED";
    }
    document.getElementById("pf-doc-title").textContent = d.download_filename || "preview-accessible.pdf";
    document.getElementById("pf-engine-line").textContent =
      (d.verapdf_ready ? "Verified live with veraPDF 1.30.3 (ISO 14289-1:2014)" : "Structural Preview Inspector") +
      " · Files retained: " + (d.files_retained || 0);

    const rulesEl = document.getElementById("pf-kpi-rules");
    rulesEl.textContent = d.before_failed_rules + " -> " + d.after_failed_rules;
    rulesEl.className = "val " + (d.verapdf_compliant ? "ok" : "brand");
    document.getElementById("pf-kpi-rules-hint").textContent = d.verapdf_compliant
      ? "100% compliant in veraPDF 1.30.3"
      : "Remaining issues need Desktop Studio (fonts/OCR/forms)";

    const checksEl = document.getElementById("pf-kpi-checks");
    checksEl.textContent = Number(d.before_failed_checks || 0).toLocaleString("en-US") + " -> " + Number(d.after_failed_checks || 0).toLocaleString("en-US");
    checksEl.className = "val " + (d.after_failed_checks === 0 ? "ok" : "brand");

    document.getElementById("pf-kpi-pages").textContent = d.pages_processed + " / " + d.total_source_pages + " Pages";
    document.getElementById("pf-kpi-trunc").textContent = d.truncated_to_preview
      ? "Truncated to first 2 pages (unlock unlimited pages below)"
      : "All " + d.total_source_pages + " page(s) processed in free preview";

    const fixesUl = document.getElementById("pf-fixes-list");
    const fixes = d.fixes_applied || [];
    fixesUl.innerHTML = fixes.length
      ? fixes.map((fx) => "<li>" + esc(fx) + "</li>").join("")
      : "<li>No structural changes needed — PDF already structurally valid.</li>";

    const u = d.upgrade_ctas || {};
    document.getElementById("pf-cta-solo").href = u.solo_early50_url || "https://pdfuapartnerdesk.gumroad.com/l/pdfua-express-5-pages/EARLY50?wanted=true";
    document.getElementById("pf-cta-agency").href = u.agency_early50_url || "https://pdfuapartnerdesk.gumroad.com/l/pdfua-remediation-12-pages/EARLY50?wanted=true";
    document.getElementById("pf-cta-fiverr").href = u.fiverr_dfy_url || "https://www.fiverr.com/pdfua_partner/remediate-pdf-documents-for-ada-title-ii-wcag-aa-and-pdf-ua-accessibility";
    document.getElementById("pf-cta-mailto").href = u.mailto_url || "mailto:drtheloc@gmail.com";

    previewBox.style.display = "block";
    previewBox.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  document.getElementById("btn-download-preview-pdf").addEventListener("click", () => {
    if (!lastPreview || !lastPreview.preview_pdf_base64) return;
    const binStr = atob(lastPreview.preview_pdf_base64);
    const bytes = new Uint8Array(binStr.length);
    for (let i = 0; i < binStr.length; i++) bytes[i] = binStr.charCodeAt(i);
    const blob = new Blob([bytes], { type: "application/pdf" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = lastPreview.download_filename || "preview-2p-accessible.pdf";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  });

  document.getElementById("btn-run-preview-from-audit").addEventListener("click", () => {
    if (lastFile) {
      setMode("preview-fix");
      runPreviewFix(lastFile);
    } else {
      fileInput.click();
    }
  });

  async function runCheck(file) {
    errorBox.style.display = "none";
    resultsBox.style.display = "none";
    if (file.size > 20 * 1024 * 1024) {
      errorBox.innerHTML = "<strong>File exceeds 20 MB limit.</strong> For large 50&ndash;500 page reports, email <a href='mailto:drtheloc@gmail.com'>drtheloc@gmail.com</a> for a free workstation audit.";
      errorBox.style.display = "block";
      return;
    }
    statusBox.textContent = "Running veraPDF 1.30.3 ISO 14289-1 (PDF/UA-1) & structural inspection...";
    statusBox.style.display = "block";
    try {
      const resp = await fetch("./api/check", {
        method: "POST",
        headers: {
          "Content-Type": "application/pdf",
          "X-Filename": encodeURIComponent(file.name || "document.pdf")
        },
        body: file
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || ("Validation error (" + resp.status + ")"));
      }
      lastReport = data;
      renderReport(data);
    } catch (err) {
      errorBox.textContent = "Error: " + (err && err.message ? err.message : String(err));
      errorBox.style.display = "block";
    } finally {
      statusBox.style.display = "none";
    }
  }

  function renderReport(d) {
    const pill = document.getElementById("verdict-pill");
    if (d.is_compliant) {
      pill.className = "pill pill-ok";
      pill.textContent = "PASS · 0 FAILED PDF/UA-1 RULES";
    } else {
      pill.className = "pill pill-err";
      pill.textContent = "NON-COMPLIANT · FAILS PDF/UA-1";
    }
    document.getElementById("doc-title").textContent = d.filename + " (" + (d.file_size_bytes / 1024).toFixed(1) + " KB)";
    document.getElementById("engine-line").textContent = d.engine + " · Completed in " + d.elapsed_seconds + "s";

    const rulesEl = document.getElementById("kpi-rules");
    rulesEl.textContent = d.failed_rules;
    rulesEl.className = "val " + (d.is_compliant ? "ok" : "err");
    document.getElementById("kpi-checks").textContent = Number(d.failed_checks || 0).toLocaleString("en-US") + " failed clause checks";

    const q = d.quote || {};
    const st = d.structure || {};
    const pages = Number(q.pages || st.pages || 1);
    const licUsd = pages <= 15 ? 9.50 : (pages <= 150 ? 32.50 : 97.50);
    document.getElementById("kpi-pages").textContent = pages + (pages === 1 ? " Page" : " Pages");
    document.getElementById("kpi-tier").textContent = q.tier_label || "";
    document.getElementById("kpi-quote").textContent = fmtUsd(licUsd);
    document.getElementById("kpi-wholesale").textContent = "EARLY50 50% Off (vs. " + fmtUsd(q.retail_total_usd) + " manual vendor)";

    const checks = [
      [st.is_tagged, "Tagged Structure (/StructTreeRoot)"],
      [st.has_pdfua_id, "PDF/UA-1 XMP ID (pdfuaid:part=1)"],
      [Boolean(st.language), "Document Language (" + (st.language || "Missing") + ")"],
      [st.has_title && st.display_doc_title, "Accessible Title & DisplayDocTitle"],
      [st.figures_missing_alt === 0, "Figures Alt-Text (" + st.figures_missing_alt + " missing /Alt)"],
      [st.form_fields_missing_tu === 0, "Form Tooltips (" + st.form_fields_total + " fields, " + st.form_fields_missing_tu + " missing /TU)"],
      [!st.likely_scanned_ocr, st.likely_scanned_ocr ? "Scanned Image PDF (Needs OCR)" : "Searchable Text Layer Present"],
      [!st.is_cryptographically_signed, st.is_cryptographically_signed ? "Cryptographic Signature Lock Detected" : "Ready for Lossless Remediation"]
    ];
    document.getElementById("struct-grid").innerHTML = checks.map(([ok, label]) =>
      "<div class='chk'><strong class='" + (ok ? "ok" : "err") + "'>" + (ok ? "Pass:" : "Issue:") + "</strong> " + esc(label) + "</div>"
    ).join("");

    document.getElementById("cta-headline").textContent = d.is_compliant
      ? "Automated Rules Passed — Need Human NVDA Reading-Order Verification or Batch CLI?"
      : "Fix \"" + d.filename + "\" (" + pages + "p) to 0 Failed Rules — Studio Solo $9.50 (EARLY50) or Done-For-You $45";
    document.getElementById("cta-sub").textContent =
      "Try our Free 2-Page Auto-Fix Preview above, run PDF Accessibility Studio v1.6.1 offline with 50% off EARLY50 ($9.50 Solo / $32.50 Agency / $97.50 Commercial), or book Done-For-You remediation ($45 up to 15 pages on Fiverr).";

    document.getElementById("cta-checkout").href = d.cta.solo_early50_url || d.cta.direct_checkout_url;
    document.getElementById("cta-agency").href = d.cta.agency_early50_url || d.cta.checkout_65_url;
    document.getElementById("cta-fiverr").href = d.cta.fiverr_dfy_url || d.cta.upwork_catalog_url;
    document.getElementById("cta-mailto").href = d.cta.mailto_url;

    const listEl = document.getElementById("issues-list");
    if (!d.findings || d.findings.length === 0) {
      listEl.innerHTML = "<div class='issue-item issue-pass'><strong style='color:var(--ok);'>Zero ISO 14289-1 (PDF/UA-1) Rule Failures Detected</strong><p style='margin:6px 0 0; color:var(--ink-2);'>This PDF passed all automated veraPDF 1.30.3 PDF/UA-1 clauses. Verify semantic alt-text accuracy and logical reading order in NVDA before publishing.</p></div>";
    } else {
      listEl.innerHTML = d.findings.map((f) => {
        const pCls = f.severity === "CRITICAL" ? "pill-err" : "pill-warn";
        return "<div class='issue-item'>" +
          "<div class='issue-head'>" +
            "<strong>Clause " + esc(f.clause) + " — " + esc(f.title) + "</strong>" +
            "<span class='pill " + pCls + "'>" + esc(f.severity) + " · " + Number(f.failed_checks).toLocaleString("en-US") + " checks</span>" +
          "</div>" +
          "<div class='mono' style='margin-top:4px;'>" + esc(f.wcag) + "</div>" +
          "<p style='margin:8px 0 0; color:var(--ink-2); font-size:0.93rem;'><strong>User &amp; ADA Impact:</strong> " + esc(f.impact) + "</p>" +
        "</div>";
      }).join("");
    }
    resultsBox.style.display = "block";
    resultsBox.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  document.getElementById("btn-download-report").addEventListener("click", () => {
    if (!lastReport) return;
    const d = lastReport;
    const rows = (d.findings || []).map((f) =>
      "<tr><td><strong>ISO 14289-1 §" + esc(f.clause) + "</strong></td><td><strong>" + esc(f.title) + "</strong><br><small>" + esc(f.wcag) + "</small></td><td>" + esc(f.impact) + "</td><td>" + f.failed_checks + "</td></tr>"
    ).join("") || "<tr><td colspan='4'>0 failed rules detected in veraPDF 1.30.3.</td></tr>";
    const htmlDoc = "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>PDF/UA-1 Audit Report — " + esc(d.filename) + "</title>" +
      "<style>body{font-family:sans-serif;max-width:960px;margin:32px auto;padding:0 20px;color:#0f172a;line-height:1.5}table{width:100%;border-collapse:collapse;margin:18px 0}th,td{border:1px solid #cbd5e1;padding:10px;text-align:left;vertical-align:top}th{background:#f1f5f9}.box{background:#eff6ff;border:1px solid #bfdbfe;border-left:4px solid #1d4ed8;border-radius:8px;padding:18px;margin:20px 0}</style></head><body>" +
      "<h1>ADA Title II &amp; ISO 14289-1 (PDF/UA-1) Audit Report</h1>" +
      "<p><strong>File:</strong> " + esc(d.filename) + " (" + d.quote.pages + " pages) &middot; <strong>Verdict:</strong> " + (d.is_compliant ? "PASS (0 Failed Rules)" : "NON-COMPLIANT (" + d.failed_rules + " Failed Rules / " + d.failed_checks + " Failed Checks)") + "</p>" +
      "<div class='box'><strong>Self-Serve Studio Licenses (EARLY50 50% off):</strong> Solo $9.50 ($19 list) &middot; Agency $32.50 ($65 list) &middot; Done-For-You Fiverr ($45 up to 15p).<br>" +
      "Studio Solo: <a href='" + esc(d.cta.solo_early50_url) + "'>" + esc(d.cta.solo_early50_url) + "</a> &middot; Done-For-You Fiverr: <a href='" + esc(d.cta.fiverr_dfy_url) + "'>" + esc(d.cta.fiverr_dfy_url) + "</a> &middot; Direct B2B Pilot: drtheloc@gmail.com</div>" +
      "<table><thead><tr><th scope='col'>Clause</th><th scope='col'>Issue &amp; WCAG Criterion</th><th scope='col'>Assistive Tech Impact</th><th scope='col'>Failed Checks</th></tr></thead><tbody>" + rows + "</tbody></table>" +
      "</body></html>";
    const blob = new Blob([htmlDoc], { type: "text/html;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = d.filename.replace(/\.pdf$/i, "") + "-pdfua-audit-report.html";
    a.click();
  });
})();
</script>
</body>
</html>
"""


class CheckerHTTPHandler(BaseHTTPRequestHandler):
    server_version = "PDFUAChecker/1.6"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors()
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/health":
            discovered = _discover_verapdf()
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "verapdf_ready": discovered is not None,
                    "preview_fix_ready": True,
                    "max_preview_pages": MAX_PREVIEW_PAGES,
                    "max_upload_mb": 20,
                    "files_retained": 0,
                },
            )
            return
        if path in ("/", "/index.html"):
            raw = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self._send_cors()
            self.end_headers()
            self.wfile.write(raw)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path not in ("/api/check", "/api/preview-fix"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return

        try:
            clen = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            clen = 0
        if clen <= 0:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Empty request body."})
            return
        if clen > MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "File exceeds the 20 MB free checker limit."},
            )
            return

        body = self.rfile.read(clen)
        try:
            pdf_bytes, filename = _extract_upload(self.headers, body)
            if len(pdf_bytes) > MAX_UPLOAD_BYTES:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": "File exceeds the 20 MB free checker limit."},
                )
                return
            if path == "/api/preview-fix":
                report = preview_fix_pdf_bytes(pdf_bytes, filename=filename)
            else:
                report = analyze_pdf_bytes(pdf_bytes, filename=filename)
            self._send_json(HTTPStatus.OK, report)
        except ValueError as exc:
            status = (
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                if "20 MB" in str(exc)
                else HTTPStatus.BAD_REQUEST
            )
            self._send_json(status, {"error": str(exc)})
        except Exception as exc:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"Checker error: {exc}"},
            )


def main() -> None:
    port = int(os.environ.get("PORT", "7860"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), CheckerHTTPHandler)
    print(f"PDF/UA & ADA Title II Checker listening on 0.0.0.0:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
