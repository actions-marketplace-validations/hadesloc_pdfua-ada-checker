from __future__ import annotations

import base64
from http.server import ThreadingHTTPServer
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
import uuid

import pikepdf

import app


def _make_multipage_untagged_pdf(num_pages: int) -> bytes:
    """Create a minimal valid multi-page untagged PDF in memory via pikepdf."""
    pdf = pikepdf.Pdf.new()
    for idx in range(num_pages):
        page = pdf.add_blank_page(page_size=(612, 792))
        stream_data = f"q 0 0 100 20 re f Q BT /F1 12 Tf 72 720 Td (Page {idx + 1} hello) Tj ET".encode("ascii")
        page["/Contents"] = pdf.make_stream(stream_data)
        font_dict = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/Type1"),
            BaseFont=pikepdf.Name("/Helvetica"),
        )
        page["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font_dict))
        annot = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"),
                Subtype=pikepdf.Name("/Link"),
                Rect=pikepdf.Array([72, 700, 200, 720]),
                A=pikepdf.Dictionary(
                    S=pikepdf.Name("/URI"),
                    URI=pikepdf.String("https://example.org/ada"),
                ),
            )
        )
        page["/Annots"] = pikepdf.Array([annot])
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _build_multipart(pdf_bytes: bytes, filename: str) -> tuple[bytes, str]:
    boundary = f"----PreviewFixTest{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + pdf_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


class PreviewFixApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.CheckerHTTPHandler)
        self.port = self.srv.server_port
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()

    def test_contact_email_and_ctas_updated(self) -> None:
        self.assertEqual(app.CONTACT_EMAIL, "drtheloc@gmail.com")
        self.assertNotIn("halocrypto101@gmail.com", app.INDEX_HTML)
        self.assertIn("drtheloc@gmail.com", app.INDEX_HTML)
        self.assertIn("EARLY50", app.INDEX_HTML)
        self.assertIn("Auto-Fix First 2 Pages Free", app.INDEX_HTML)
        self.assertIn("Download Fixed 2-Page Preview PDF", app.INDEX_HTML)
        self.assertEqual(app.MAX_PREVIEW_PAGES, 2)

    def test_health_endpoint_reports_preview_fix_ready(self) -> None:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
            data = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertTrue(data["preview_fix_ready"])
        self.assertEqual(data["max_preview_pages"], 2)
        self.assertEqual(data["files_retained"], 0)

    def test_preview_fix_truncates_over_two_pages_and_repairs_structure(self) -> None:
        src_bytes = _make_multipage_untagged_pdf(5)
        body, ctype = _build_multipart(src_bytes, "city-budget-2026.pdf")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/preview-fix",
            data=body,
            headers={"Content-Type": ctype},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
            data = json.loads(resp.read().decode("utf-8"))

        self.assertTrue(data["ok"])
        self.assertEqual(data["pages_processed"], 2)
        self.assertEqual(data["total_source_pages"], 5)
        self.assertTrue(data["truncated_to_preview"])
        self.assertEqual(data["files_retained"], 0)
        self.assertEqual(data["download_filename"], "city-budget-2026-2p-preview-accessible.pdf")
        self.assertGreater(data["before_failed_rules"], data["after_failed_rules"])
        self.assertGreaterEqual(len(data["fixes_applied"]), 4)

        ctas = data["upgrade_ctas"]
        self.assertEqual(ctas["contact_email"], "drtheloc@gmail.com")
        self.assertIn("/EARLY50?wanted=true", ctas["solo_early50_url"])
        self.assertIn("/EARLY50?wanted=true", ctas["agency_early50_url"])
        self.assertIn("fiverr.com/pdfua_partner", ctas["fiverr_dfy_url"])
        self.assertIn("drtheloc%40gmail.com", ctas["mailto_url"])

        fixed_bytes = base64.b64decode(data["preview_pdf_base64"])
        self.assertTrue(fixed_bytes.startswith(b"%PDF-"))
        with pikepdf.Pdf.open(io.BytesIO(fixed_bytes)) as fixed_pdf:
            self.assertEqual(len(fixed_pdf.pages), 2)
            self.assertEqual(str(fixed_pdf.Root.get("/Lang")), "en-US")
            self.assertTrue(bool(fixed_pdf.Root["/ViewerPreferences"]["/DisplayDocTitle"]))
            self.assertTrue(bool(fixed_pdf.Root["/MarkInfo"]["/Marked"]))
            self.assertIn("/StructTreeRoot", fixed_pdf.Root)
            xmp = bytes(fixed_pdf.Root.Metadata.read_bytes()).decode("utf-8")
            self.assertIn("pdfuaid:part>1<", xmp)
            self.assertIn("dc:title", xmp)
            for pg in fixed_pdf.pages:
                self.assertEqual(str(pg.get("/Tabs")), "/S")
                annot = pg.Annots[0]
                self.assertTrue(str(annot.get("/Contents", "")).startswith("Link:"))

    def test_preview_fix_single_page_not_truncated(self) -> None:
        src_bytes = _make_multipage_untagged_pdf(1)
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/preview-fix",
            data=src_bytes,
            headers={"Content-Type": "application/pdf", "X-Filename": "one-pager.pdf"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))

        self.assertTrue(data["ok"])
        self.assertEqual(data["pages_processed"], 1)
        self.assertEqual(data["total_source_pages"], 1)
        self.assertFalse(data["truncated_to_preview"])
        self.assertEqual(data["files_retained"], 0)
        fixed_bytes = base64.b64decode(data["preview_pdf_base64"])
        self.assertTrue(fixed_bytes.startswith(b"%PDF-"))

    def test_preview_fix_rejects_non_pdf(self) -> None:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/preview-fix",
            data=b"not a pdf at all",
            headers={"Content-Type": "application/pdf"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
