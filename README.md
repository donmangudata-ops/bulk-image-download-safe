# bulk-image-download-safe

Download images from a list of image links and web pages into ZIP files, without running out of memory. One Python file, standard library only.

Many bulk image tools keep every image in memory and build one ZIP at the end. With a few thousand images the run dies with "out of memory", and you get nothing. This script avoids that:

| Problem | What the script does |
|---|---|
| All images held in memory | Writes each image into the ZIP as soon as it arrives. Only one image is in memory at a time. |
| One huge ZIP | Starts a new ZIP part after a size you choose (`--part-mb`, default 500). |
| The same image under many URLs | Saves each file once (SHA-256 of the bytes). |
| 20 MB originals | Skips files above `--max-mb` (default 10) before it downloads them, when the server sends the size. |
| A bad link stops the job | Logs the error and goes on. |

## Install

```bash
git clone https://github.com/donmangudata-ops/bulk-image-download-safe
cd bulk-image-download-safe
python bulk_images.py --help
```

Python 3.8 or later. No packages to install.

## Usage

Put one image link or web page per line in a text file:

```
https://example.com/photos/1.jpg
https://example.com/gallery/
```

Then:

```bash
python bulk_images.py urls.txt -o out
python bulk_images.py urls.txt -o out --max-mb 5 --min-width 300 --part-mb 200
```

From a web page the script takes `img` and `source` tags (the widest `srcset` candidate), lazy-load attributes (`data-src`, `data-srcset`, `data-lazy-src`) and `og:image` / `twitter:image`.

Output in the `out` folder:

- `images-part1.zip`, `images-part2.zip`, ... with files named by content hash
- `index.csv` with one row per image: status (saved, duplicate, too big, too narrow, not an image, failed), source URL, page, file, ZIP part, format, width, height, bytes

The script reads the format and size from the file bytes (PNG, JPEG, GIF; WebP and SVG without size). `--min-width` only applies when the width is known.

## Be polite

The script waits 0.2 seconds between requests (`--delay`). Set `--user-agent` to something that identifies you. Only download images you are allowed to copy.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests start a local web server and check dedupe, size and width filters, page extraction, ZIP parts and error handling.

## Hosted version (optional)

If you don't want to run this yourself, I also sell a hosted version on Apify: [Bulk Image Downloader](https://apify.com/conserving_celerytop/bulk-image-downloader?utm_source=github&utm_medium=readme&utm_campaign=bulk-image-download-safe). It runs in the cloud, adds CSS background images and folder layouts, and connects to Make, n8n and Zapier. It charges $1 per 1,000 images saved, and skipped files are free.

Disclosure: I built that Actor, and it is paid. This repository is free and works without it. The code was written with help from an AI assistant and is covered by the tests above.

## License

MIT
