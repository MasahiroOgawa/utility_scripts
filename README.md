# utility_scripts
This is a collection of utility shell scripts.

## `script/allround_downloader.py` — paste-the-URL video downloader

Downloads the main video from almost any site (YouTube, missav, jable, njav, and generic pages) as a clean `.mp4`. The only required input is a copied URL.

Extraction is always done by yt-dlp; the download engine is then chosen by protocol:

- **HLS (m3u8)** — a custom resumable segment downloader: each `.ts` is written to disk and skipped on restart (perfect resume), AES-128 is decrypted, and segments are streamed into a single file then muxed to mp4 with `ffmpeg -c copy` (no failure on large >2 GB outputs).
- **everything else** (YouTube DASH/progressive) — yt-dlp itself, resuming via byte ranges and merging `bestvideo+bestaudio` into mp4.

Features:

- Simple tkinter GUI with a **Paste** button.
- When the main video can't be auto-detected, shows several candidates with tiny frame-grab thumbnails and lets you pick.
- `curl_cffi` browser impersonation to get past Cloudflare 403; generic-extractor and HTML-scrape fallbacks when a site extractor refuses.
- Resumable downloads, live percentage, and max-resolution selection.

Prereqs (one-time): `ffmpeg` on `PATH`; Python deps are declared in `pyproject.toml` and installed by `uv`.

```bash
sudo apt install -y ffmpeg   # if not already installed
uv sync
```

Run:

```bash
uv run script/allround_downloader.py                      # GUI
uv run script/allround_downloader.py <URL>                # GUI pre-filled with URL
uv run script/allround_downloader.py --cli <URL> -o DIR   # headless download
uv run script/allround_downloader.py --probe <URL>        # just list detected candidates
uv run script/allround_downloader.py --cli <URL> --pick N # download candidate index N
```

Downloads default to `./downloads/` (git-ignored). Stop any time — re-running the same URL resumes from where it left off.

## iPhone ↔ Ubuntu file transfer

Two complementary scripts under `script/`. Both write files into `/home/mas/iphone-share/` — create the directory (or edit `DEST_DIR` / `SHARE_DIR` in the scripts) before first use.

### `script/cp_iphone_app_docs.sh` — pull an iPhone app's Documents over USB

Mounts a specific iOS app's Documents folder via `ifuse` and copies everything into `/home/mas/iphone-share/`. Use this for large captures (multi-GB) — USB is the only reliable path.

Prereqs (one-time):

```bash
sudo apt install -y ifuse libimobiledevice-utils ideviceinstaller
```

Run:

1. Plug iPhone in, unlock, tap **Trust** on the first-time prompt.
2. `./script/cp_iphone_app_docs.sh`

The script lists the app's Documents, copies into `/home/mas/iphone-share/`, then unmounts and cleans up `/tmp/iphone-app-docs` via an `EXIT` trap. The bundle ID is hard-coded (`com.dopymas.dopescan.3SM82K4JRQ`); edit `BUNDLE_ID` to target a different app. To discover bundle IDs: `ideviceinstaller -l`.

### `script/launch_upload_server.sh` — small-file drop over LAN

Starts a Python `uploadserver` on port 8000 serving `/home/mas/iphone-share/`. iPhone Safari opens `http://<pc-ip>:8000/upload`, picks a file in the web form, submits. No iPhone app install needed.

Prereqs (one-time):

```bash
uv tool install uploadserver
```

Run:

```bash
./script/launch_upload_server.sh
```

The script prints the URL to paste into Safari. Ctrl-C to stop.

**Size limit:** `uploadserver` uses `cgi.FieldStorage`, which buffers the whole request in memory — fine up to a few hundred MB, flaky on multi-GB. For bigger files use `cp_iphone_app_docs.sh` over USB.
