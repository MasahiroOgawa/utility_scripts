# `allround_downloader.py` — paste-the-URL video downloader

Downloads the main video from almost any site (YouTube, missav, jable, njav, and generic pages) as a clean `.mp4`. The only required input is a copied URL.

Extraction is always done by yt-dlp; the download engine is then chosen by protocol:

- **HLS (m3u8)** — a custom resumable segment downloader: each `.ts` is written to disk and skipped on restart (perfect resume), AES-128 is decrypted, and segments are streamed into a single file then muxed to mp4 with `ffmpeg -c copy` (no failure on large >2 GB outputs).
- **everything else** (YouTube DASH/progressive) — yt-dlp itself, resuming via byte ranges and merging `bestvideo+bestaudio` into mp4.

## Features

- **Browser-based GUI** with a **Paste** button — running it starts a tiny local web server and opens the page in your browser (no desktop toolkit needed).
- **Parallel downloads** — queue as many as you like (paste more URLs, or pick several candidates); up to 4 download at once and the rest wait, each with its own progress bar and Stop button.
- When the main video can't be auto-detected, shows several candidates with tiny frame-grab thumbnails and lets you pick.
- `curl_cffi` browser impersonation to get past Cloudflare 403; generic-extractor and HTML-scrape fallbacks when a site extractor refuses.
- **Browser fallback** — for pages that decrypt the player in JavaScript and hide the stream behind bot-detection (e.g. javhdporn / kingtube-theme sites), the tool drives a *private, throwaway* browser, presses play, and captures the real `.m3u8`/`.mp4` the player requests. It uses Playwright's own bundled Chromium (never your system browser), runs in a temporary profile that is deleted afterwards, and leaves no trace on the system. Some sites fingerprint automation and serve a decoy anyway, so this is best-effort.
- Resumable downloads, live percentage, and max-resolution selection.

## Prereqs (one-time)

`ffmpeg` on `PATH`, plus `uv sync`. All Python deps (and the Python interpreter itself) are managed by `uv` — nothing relies on the system Python, so the script runs anywhere `uv` is installed.

```bash
sudo apt install -y ffmpeg chromium   # ffmpeg always; chromium for the browser fallback
uv sync
```

The browser fallback needs a Chromium/Chrome browser. It uses a system-installed one if present (`apt install chromium`, `chromium-browser`, or Google Chrome) — so plain `uv sync` is all the Python setup you need. If you'd rather not install one system-wide, run `uv run playwright install chromium` to fetch Playwright's own (~150 MB) instead. Skip both if you never hit a JS-gated site; the tool prints a reminder if it's ever needed.

## Run

```bash
uv run script/allround_downloader.py                      # GUI
uv run script/allround_downloader.py <URL>                # GUI pre-filled with URL
uv run script/allround_downloader.py --cli <URL> -o DIR   # headless download
uv run script/allround_downloader.py --probe <URL>        # just list detected candidates
uv run script/allround_downloader.py --cli <URL> --pick N # download candidate index N
```

Downloads default to `./downloads/` (git-ignored). Stop any time — re-running the same URL resumes from where it left off.
