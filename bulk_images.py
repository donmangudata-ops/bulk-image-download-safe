"""bulk-image-download-safe: download images from links and web pages without running out of memory.

Each image is written to the ZIP as soon as it arrives, identical files are saved once,
large files are skipped, and the archive is split into parts. Standard library only.

Usage:
    python bulk_images.py urls.txt -o out
    python bulk_images.py urls.txt -o out --max-mb 5 --min-width 300 --part-mb 500
"""
import argparse
import csv
import hashlib
import os
import struct
import sys
import time
import zipfile
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DEFAULT_UA = "bulk-image-download-safe/1.0 (+https://github.com/donmangudata-ops/bulk-image-download-safe)"
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".svg", ".tif", ".tiff", ".ico")


def sniff(data):
    """Return (format, width, height) from the first bytes. Width/height are None when unknown."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return "png", w, h
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        w, h = struct.unpack("<HH", data[6:10])
        return "gif", w, h
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return "jpg", w, h
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg
        return "jpg", None, None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", None, None
    if data.lstrip()[:5] in (b"<?xml", b"<svg ") or b"<svg" in data[:500]:
        return "svg", None, None
    return None, None, None


class ImgParser(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.found = base, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("img", "source"):
            srcset = a.get("srcset") or a.get("data-srcset")
            if srcset:  # pick the widest candidate
                best, best_w = None, -1
                for part in srcset.split(","):
                    bits = part.strip().split()
                    if not bits:
                        continue
                    w = int(bits[1][:-1]) if len(bits) > 1 and bits[1].endswith("w") and bits[1][:-1].isdigit() else 0
                    if w > best_w:
                        best, best_w = bits[0], w
                if best:
                    self.found.append(urljoin(self.base, best))
            for key in ("src", "data-src", "data-lazy-src"):
                if a.get(key) and not a[key].startswith("data:"):
                    self.found.append(urljoin(self.base, a[key]))
        elif tag == "meta" and a.get("property", a.get("name", "")) in ("og:image", "twitter:image") and a.get("content"):
            self.found.append(urljoin(self.base, a["content"]))


class Downloader:
    def __init__(self, out_dir, max_mb=10, min_width=0, part_mb=500, user_agent=DEFAULT_UA, delay=0.2, timeout=30):
        self.out_dir, self.max_bytes = out_dir, int(max_mb * 1024 * 1024)
        self.min_width, self.part_limit = min_width, int(part_mb * 1024 * 1024)
        self.headers, self.delay, self.timeout = {"User-Agent": user_agent}, delay, timeout
        self.seen, self.part, self.part_size, self.zip = set(), 0, 0, None
        os.makedirs(out_dir, exist_ok=True)
        self.index = open(os.path.join(out_dir, "index.csv"), "w", newline="", encoding="utf-8")
        self.rows = csv.writer(self.index)
        self.rows.writerow(["status", "url", "page", "file", "zip_part", "format", "width", "height", "bytes"])
        self._new_part()

    def _new_part(self):
        if self.zip:
            self.zip.close()
        self.part += 1
        self.part_size = 0
        self.zip = zipfile.ZipFile(os.path.join(self.out_dir, f"images-part{self.part}.zip"), "w", zipfile.ZIP_STORED)

    def _get(self, url, limit):
        time.sleep(self.delay)
        with urlopen(Request(url, headers=self.headers), timeout=self.timeout) as r:
            size = int(r.headers.get("Content-Length") or 0)
            if size > limit:
                return None, r.headers.get("Content-Type", "")
            return r.read(limit + 1), r.headers.get("Content-Type", "")

    def image_urls(self, url):
        """A direct image link gives itself; a web page gives the images on it."""
        if url.split("?")[0].lower().endswith(IMAGE_EXT):
            return [url], ""
        data, ctype = self._get(url, 5 * 1024 * 1024)
        if data is not None and ctype.startswith("image/"):
            return [url], ""
        p = ImgParser(url)
        p.feed((data or b"").decode("utf-8", "replace"))
        return list(dict.fromkeys(p.found)), url

    def save(self, url, page):
        try:
            data, _ = self._get(url, self.max_bytes)
        except (HTTPError, URLError, OSError) as e:
            self.rows.writerow([f"failed: {e}", url, page, "", "", "", "", "", ""])
            return
        if data is None or len(data) > self.max_bytes:
            self.rows.writerow(["skipped: too big", url, page, "", "", "", "", "", ""])
            return
        fmt, w, h = sniff(data)
        if fmt is None:
            self.rows.writerow(["skipped: not an image", url, page, "", "", "", "", "", len(data)])
            return
        if self.min_width and w is not None and w < self.min_width:
            self.rows.writerow(["skipped: too narrow", url, page, "", "", fmt, w, h, len(data)])
            return
        digest = hashlib.sha256(data).hexdigest()
        if digest in self.seen:
            self.rows.writerow(["skipped: duplicate", url, page, "", "", fmt, w, h, len(data)])
            return
        self.seen.add(digest)
        if self.part_size and self.part_size + len(data) > self.part_limit:
            self._new_part()
        name = f"{digest[:16]}.{fmt}"
        self.zip.writestr(name, data)  # written now, nothing is kept in memory
        self.part_size += len(data)
        self.rows.writerow(["saved", url, page, name, self.part, fmt, w, h, len(data)])

    def run(self, inputs):
        for item in inputs:
            try:
                urls, page = self.image_urls(item)
            except (HTTPError, URLError, OSError) as e:
                self.rows.writerow([f"failed: {e}", item, "", "", "", "", "", "", ""])
                continue
            for u in urls:
                self.save(u, page)
        self.zip.close()
        self.index.close()
        return len(self.seen), self.part


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="text file: one image link or web page per line")
    p.add_argument("-o", "--out", default="images")
    p.add_argument("--max-mb", type=float, default=10, help="skip files larger than this (default 10)")
    p.add_argument("--min-width", type=int, default=0, help="skip images narrower than this, when the width is known")
    p.add_argument("--part-mb", type=float, default=500, help="start a new ZIP part after this size (default 500)")
    p.add_argument("--user-agent", default=DEFAULT_UA)
    p.add_argument("--delay", type=float, default=0.2, help="seconds between requests")
    a = p.parse_args(argv)
    with open(a.input, encoding="utf-8") as f:
        inputs = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    saved, parts = Downloader(a.out, a.max_mb, a.min_width, a.part_mb, a.user_agent, a.delay).run(inputs)
    print(f"saved {saved} images in {parts} ZIP part(s); details in {a.out}/index.csv", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
