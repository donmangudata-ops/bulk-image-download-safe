import csv
import http.server
import os
import struct
import sys
import tempfile
import threading
import unittest
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bulk_images as bi  # noqa: E402


def png(w, h, seed=0):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes([seed % 256]) * (w * 3) for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class BulkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        w = lambda n, d: open(os.path.join(cls.dir, n), "wb").write(d if isinstance(d, bytes) else d.encode())
        w("wide.png", png(400, 10, 1))
        w("copy.png", png(400, 10, 1))      # same bytes as wide.png
        w("narrow.png", png(50, 10, 2))
        w("big.png", png(400, 10, 3) + os.urandom(3 * 1024 * 1024))
        w("notimage.png", "hello")
        w("page.html", '<html><head><meta property="og:image" content="/og.png"></head><body>'
                       '<img src="/wide.png"><img srcset="/small.png 100w, /large.png 800w">'
                       '<img data-src="/lazy.png"><img src="data:image/png;base64,xx"></body></html>')
        w("og.png", png(500, 5, 4))
        w("large.png", png(800, 5, 5))
        w("lazy.png", png(600, 5, 6))
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=cls.dir, **k))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def run_job(self, inputs, **kw):
        out = tempfile.mkdtemp()
        saved, parts = bi.Downloader(out, delay=0, **kw).run([self.base + i for i in inputs])
        rows = list(csv.DictReader(open(os.path.join(out, "index.csv"))))
        return out, saved, parts, rows

    def test_links_dedupe_size_and_errors(self):
        out, saved, parts, rows = self.run_job(["/wide.png", "/copy.png", "/big.png", "/notimage.png", "/missing.png"], max_mb=1)
        status = {r["url"].rsplit("/", 1)[1]: r["status"] for r in rows}
        self.assertEqual(saved, 1)
        self.assertEqual(status["copy.png"], "skipped: duplicate")
        self.assertEqual(status["big.png"], "skipped: too big")
        self.assertEqual(status["notimage.png"], "skipped: not an image")
        self.assertTrue(status["missing.png"].startswith("failed"))
        self.assertEqual(len(zipfile.ZipFile(os.path.join(out, "images-part1.zip")).namelist()), 1)

    def test_page_extraction_and_min_width(self):
        out, saved, parts, rows = self.run_job(["/page.html", "/narrow.png"], min_width=300)
        saved_names = sorted(r["url"].rsplit("/", 1)[1] for r in rows if r["status"] == "saved")
        self.assertEqual(saved_names, ["large.png", "lazy.png", "og.png", "wide.png"])  # widest srcset item only
        self.assertIn("skipped: too narrow", [r["status"] for r in rows])

    def test_zip_parts(self):
        out, saved, parts, rows = self.run_job(["/wide.png", "/og.png", "/large.png", "/lazy.png"], part_mb=0.000001)
        self.assertEqual(saved, 4)
        self.assertEqual(parts, 4)

    def test_sniff_png_size(self):
        self.assertEqual(bi.sniff(png(123, 45)), ("png", 123, 45))


if __name__ == "__main__":
    unittest.main()
