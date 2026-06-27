#!/usr/bin/env python3
"""
allround_downloader.py — a paste-the-URL video downloader for almost any site.

Goals (see project request):
  * Works on general sites incl. YouTube, missav123.com, jable.tv, njavtv.com.
  * Downloads the *main* video. When the main one can't be auto-detected it shows
    several candidates with tiny preview thumbnails and lets the user pick.
  * Simple GUI — the only required input is a copied URL.
  * Incremental / resumable: stop any time and restart picks up where it left off,
    finally producing a clean .mp4.
  * Grabs the highest available resolution.
  * Shows a live percentage while downloading.
  * Does NOT choke on large outputs (>2 GB) — everything is streamed, never buffered
    whole in memory, and HLS is muxed with ffmpeg (64-bit offsets).

Engine strategy
  Extraction is always done by yt-dlp (it knows the site-specific quirks for
  YouTube / jable / missav / njav and thousands more). The *download* engine is then
  chosen by protocol:
    - HLS (m3u8)  -> a custom segment downloader: each .ts is written to disk and
                     skipped on restart (perfect resume), AES-128 is decrypted, and
                     the segments are streamed into a single .ts then muxed to mp4
                     with `ffmpeg -c copy` (no >2 GB failure).
    - everything  -> yt-dlp itself (DASH/progressive resume via byte ranges, merges
      else            bestvideo+bestaudio to mp4).
  If yt-dlp can't extract at all, the page HTML is scraped for .m3u8/.mp4 URLs and
  those become candidates.

Usage
    python allround_downloader.py                 # launch the GUI
    python allround_downloader.py <URL>           # GUI pre-filled with URL
    python allround_downloader.py --cli <URL>     # headless download
    python allround_downloader.py --probe <URL>   # just list detected candidates
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import requests
import yt_dlp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Optional Cloudflare/anti-bot bypass via browser TLS impersonation.
try:
    from curl_cffi import requests as cffi_requests

    _IMPERSONATE = "chrome"
except Exception:  # pragma: no cover - optional dependency
    cffi_requests = None
    _IMPERSONATE = None

DEFAULT_OUTDIR = os.path.join(os.getcwd(), "downloads")
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def make_session():
    """A requests-compatible session that impersonates a browser when possible."""
    if cffi_requests is not None:
        return cffi_requests.Session(impersonate=_IMPERSONATE)
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _ytdlp_base_opts() -> dict:
    """Common yt-dlp options, including impersonation when curl_cffi is present."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "http_headers": {"User-Agent": USER_AGENT},
    }
    if _IMPERSONATE:
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget

            opts["impersonate"] = ImpersonateTarget("chrome")
        except Exception:
            pass
    return opts


class StopDownload(Exception):
    """Raised internally to abort a download so it can be resumed later."""


@dataclass
class Candidate:
    """One choosable video on a page."""

    title: str
    download_url: str          # webpage URL (yt-dlp) or direct media URL
    is_ytdlp: bool             # True -> hand the URL back to yt-dlp to extract
    resolution: str = "?"
    duration: Optional[float] = None
    filesize: Optional[int] = None
    thumbnail_url: Optional[str] = None
    http_headers: dict = field(default_factory=dict)
    info: Optional[dict] = None  # raw yt-dlp info dict when available

    @property
    def label(self) -> str:
        bits = [self.title or "video"]
        if self.resolution and self.resolution != "?":
            bits.append(self.resolution)
        if self.duration:
            bits.append(_fmt_duration(self.duration))
        if self.filesize:
            bits.append(_fmt_size(self.filesize))
        return "  ·  ".join(bits)


# --------------------------------------------------------------------------- #
# small formatting helpers
# --------------------------------------------------------------------------- #
def _fmt_size(n: Optional[float]) -> str:
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _fmt_duration(secs: Optional[float]) -> str:
    if not secs:
        return ""
    secs = int(secs)
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name or "video").strip()
    return (name[:150] or "video")


# --------------------------------------------------------------------------- #
# extraction / candidate detection
# --------------------------------------------------------------------------- #
def _best_format(info: dict) -> Optional[dict]:
    """Pick the highest-resolution downloadable format from a yt-dlp info dict."""
    formats = info.get("formats") or []
    usable = [f for f in formats if f.get("url") and f.get("vcodec") != "none"]
    if not usable:
        usable = [f for f in formats if f.get("url")]
    if not usable:
        return None
    return max(
        usable,
        key=lambda f: (
            f.get("height") or 0,
            f.get("tbr") or 0,
            f.get("filesize") or f.get("filesize_approx") or 0,
        ),
    )


def _candidate_from_info(info: dict) -> Candidate:
    fmt = _best_format(info) or {}
    height = info.get("height") or fmt.get("height")
    return Candidate(
        title=info.get("title") or info.get("id") or "video",
        download_url=info.get("webpage_url") or info.get("original_url") or info.get("url"),
        is_ytdlp=True,
        resolution=f"{height}p" if height else (info.get("resolution") or "?"),
        duration=info.get("duration"),
        filesize=info.get("filesize") or info.get("filesize_approx"),
        thumbnail_url=info.get("thumbnail"),
        info=info,
    )


def probe(url: str, log: Callable[[str], None] = print) -> list[Candidate]:
    """Return the list of downloadable candidates found at *url*."""
    ydl_opts = {
        **_ytdlp_base_opts(),
        "skip_download": True,
        "noplaylist": False,
        "extract_flat": "in_playlist",
    }
    info = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        msg = str(exc)
        # Some site extractors now refuse (e.g. "piracy" guard). Retry forcing the
        # generic extractor, which still finds the embedded HLS/MP4.
        if "iracy" in msg or "no longer supported" in msg:
            log("Site extractor refused; retrying with the generic extractor…")
            try:
                with yt_dlp.YoutubeDL({**ydl_opts, "force_generic_extractor": True}) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as exc2:
                log(f"generic extractor failed ({exc2}); scraping page…")
        else:
            log(f"yt-dlp extraction failed ({exc}); scraping page for media URLs…")
    if info is None:
        return _scrape_candidates(url, log)

    if not info:
        return _scrape_candidates(url, log)

    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if len(entries) == 1:
            return [_candidate_from_info(entries[0])]
        cands = []
        for e in entries:
            cands.append(
                Candidate(
                    title=e.get("title") or e.get("id") or "video",
                    download_url=e.get("url") or e.get("webpage_url"),
                    is_ytdlp=True,
                    resolution=f"{e.get('height')}p" if e.get("height") else "?",
                    duration=e.get("duration"),
                    thumbnail_url=e.get("thumbnail"),
                    info=e,
                )
            )
        return cands

    return [_candidate_from_info(info)]


def _scrape_candidates(url: str, log: Callable[[str], None]) -> list[Candidate]:
    """Last-resort: pull .m3u8 / .mp4 URLs straight out of the page HTML."""
    headers = {"User-Agent": USER_AGENT, "Referer": url}
    try:
        html = make_session().get(url, headers=headers, timeout=30).text
    except Exception as exc:
        log(f"Could not fetch page: {exc}")
        return []

    found: list[str] = []
    for m in re.finditer(r'https?://[^\s"\'<>\\]+?\.(?:m3u8|mp4)[^\s"\'<>\\]*', html):
        u = m.group(0)
        if u not in found:
            found.append(u)
    # also catch escaped JSON urls like https:\/\/...
    for m in re.finditer(r'https?:\\?/\\?/[^\s"\'<>]+?\.(?:m3u8|mp4)', html):
        u = m.group(0).replace("\\/", "/")
        if u not in found:
            found.append(u)

    log(f"Scraped {len(found)} media URL(s) from page.")
    cands = []
    for u in found:
        ext = "m3u8" if ".m3u8" in u else "mp4"
        cands.append(
            Candidate(
                title=os.path.basename(urlparse(u).path) or ext,
                download_url=u,
                is_ytdlp=False,
                resolution="HLS" if ext == "m3u8" else "mp4",
                http_headers={"User-Agent": USER_AGENT, "Referer": url},
            )
        )
    return cands


# --------------------------------------------------------------------------- #
# thumbnails (for the candidate picker)
# --------------------------------------------------------------------------- #
def make_thumbnail(cand: Candidate, max_w: int = 160) -> Optional[bytes]:
    """Return small PNG bytes previewing *cand*, or None. Cheap: one frame only."""
    # 1) a thumbnail URL provided by yt-dlp
    if cand.thumbnail_url:
        try:
            data = make_session().get(
                cand.thumbnail_url,
                headers={"User-Agent": USER_AGENT},
                timeout=15,
            ).content
            png = _png_resize(data, max_w)
            if png:
                return png
        except Exception:
            pass

    # 2) grab a single frame with ffmpeg from the media/page URL
    src = cand.download_url
    if cand.is_ytdlp and cand.info:
        fmt = _best_format(cand.info)
        if fmt and fmt.get("url"):
            src = fmt["url"]
    return _ffmpeg_frame(src, cand.http_headers, max_w)


def _ffmpeg_frame(src: str, headers: dict, max_w: int) -> Optional[bytes]:
    cmd = [FFMPEG, "-y", "-loglevel", "error"]
    if headers:
        hdr = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        cmd += ["-headers", hdr]
    cmd += ["-i", src, "-frames:v", "1", "-vf", f"scale={max_w}:-1",
            "-f", "image2pipe", "-vcodec", "png", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=40).stdout
        return out or None
    except Exception:
        return None


def _png_resize(data: bytes, max_w: int) -> Optional[bytes]:
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = im.size
        if w > max_w:
            im = im.resize((max_w, max(1, int(h * max_w / w))))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# download engines
# --------------------------------------------------------------------------- #
class Progress:
    """Carries progress info to whatever UI is listening."""

    def __init__(self, on_progress: Callable, on_log: Callable, stop: threading.Event):
        self.on_progress = on_progress      # (percent: float, status: str)
        self.on_log = on_log                # (msg: str)
        self.stop = stop


def download(cand: Candidate, outdir: str, prog: Progress) -> str:
    """Download *cand* into *outdir*; return the final mp4 path."""
    os.makedirs(outdir, exist_ok=True)

    # Decide engine: prefer custom resumable HLS downloader for m3u8 sources.
    m3u8_url, headers, title = _resolve_hls(cand, prog.on_log)
    if m3u8_url:
        return _download_hls(m3u8_url, headers, title or cand.title, outdir, prog)
    return _download_ytdlp(cand, outdir, prog)


def _resolve_hls(cand: Candidate, log) -> tuple[Optional[str], dict, Optional[str]]:
    """If the chosen candidate is HLS, return (m3u8_url, headers, title)."""
    if not cand.is_ytdlp:
        if ".m3u8" in cand.download_url:
            return cand.download_url, cand.http_headers or {"User-Agent": USER_AGENT}, None
        return None, {}, None

    # yt-dlp candidate: re-extract full info if we only have a flat entry
    info = cand.info
    if not info or not info.get("formats"):
        try:
            with yt_dlp.YoutubeDL({**_ytdlp_base_opts(), "skip_download": True}) as ydl:
                info = ydl.extract_info(cand.download_url, download=False)
        except Exception as exc:
            log(f"re-extraction failed ({exc}); will let yt-dlp handle it.")
            return None, {}, None

    fmt = _best_format(info)
    if not fmt:
        return None, {}, None
    proto = (fmt.get("protocol") or "").lower()
    if "m3u8" in proto or (fmt.get("url") and ".m3u8" in fmt["url"]):
        headers = dict(fmt.get("http_headers") or {})
        headers.setdefault("User-Agent", USER_AGENT)
        return fmt["url"], headers, info.get("title")
    # keep the freshly extracted info for yt-dlp path / thumbnails
    cand.info = info
    return None, {}, None


# ---- yt-dlp engine (YouTube, DASH, progressive mp4) ----------------------- #
def _download_ytdlp(cand: Candidate, outdir: str, prog: Progress) -> str:
    final_holder: dict = {}

    def hook(d):
        if prog.stop.is_set():
            raise StopDownload()
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            pct = (done / total * 100) if total else 0.0
            speed = _fmt_size(d.get("speed")) + "/s" if d.get("speed") else ""
            eta = f"ETA {d.get('eta')}s" if d.get("eta") else ""
            prog.on_progress(pct, f"{_fmt_size(done)}/{_fmt_size(total)}  {speed}  {eta}")
        elif d["status"] == "finished":
            prog.on_progress(100.0, "merging / post-processing…")

    def done_hook(d):
        if d["status"] == "finished":
            final_holder["path"] = d.get("info_dict", {}).get("filepath") or d.get("filename")

    outtmpl = os.path.join(outdir, "%(title)s.%(ext)s")
    ydl_opts = {
        **_ytdlp_base_opts(),
        "outtmpl": outtmpl,
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "continuedl": True,                 # resume partial files
        "retries": 20,
        "fragment_retries": 50,
        "file_access_retries": 10,
        "concurrent_fragment_downloads": 5,
        "noprogress": True,
        "progress_hooks": [hook],
        "postprocessor_hooks": [done_hook],
        "ffmpeg_location": os.path.dirname(FFMPEG) if os.path.sep in FFMPEG else None,
    }
    prog.on_log("Downloading with yt-dlp (resumable, max resolution)…")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([cand.download_url])
    except StopDownload:
        prog.on_log("Stopped — partial file kept, restart to resume.")
        raise

    path = final_holder.get("path", "")
    prog.on_log(f"Done: {path}")
    return path


# ---- custom resumable HLS engine ------------------------------------------ #
def _http_get(session: requests.Session, url: str, headers: dict, **kw) -> requests.Response:
    r = session.get(url, headers=headers, timeout=60, **kw)
    r.raise_for_status()
    return r


def _parse_m3u8(text: str, base: str) -> tuple[list[str], list[dict]]:
    """Return (segment_urls, key_per_segment). Resolves a master playlist's best variant."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # master playlist? pick the highest-bandwidth variant
    if any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines):
        best_bw, best_uri = -1, None
        for i, ln in enumerate(lines):
            if ln.startswith("#EXT-X-STREAM-INF"):
                m = re.search(r"BANDWIDTH=(\d+)", ln)
                bw = int(m.group(1)) if m else 0
                if i + 1 < len(lines) and bw > best_bw:
                    best_bw, best_uri = bw, lines[i + 1]
        if best_uri:
            return [urljoin(base, best_uri)], []  # signal: caller must recurse

    segments, keys = [], []
    cur_key = None
    for ln in lines:
        if ln.startswith("#EXT-X-KEY"):
            method = re.search(r"METHOD=([^,]+)", ln)
            uri = re.search(r'URI="([^"]+)"', ln)
            iv = re.search(r"IV=0x([0-9A-Fa-f]+)", ln)
            if method and method.group(1) == "AES-128" and uri:
                cur_key = {
                    "uri": urljoin(base, uri.group(1)),
                    "iv": bytes.fromhex(iv.group(1)) if iv else None,
                }
            else:
                cur_key = None
        elif not ln.startswith("#"):
            segments.append(urljoin(base, ln))
            keys.append(cur_key)
    return segments, keys


def _download_hls(m3u8_url: str, headers: dict, title: str, outdir: str, prog: Progress) -> str:
    session = make_session()
    headers = headers or {"User-Agent": USER_AGENT}

    prog.on_log(f"Fetching HLS playlist: {m3u8_url}")
    text = _http_get(session, m3u8_url, headers).text
    segments, keys = _parse_m3u8(text, m3u8_url)

    # recurse once into the chosen variant of a master playlist
    if segments and not keys:
        m3u8_url = segments[0]
        prog.on_log(f"Master playlist -> best variant: {m3u8_url}")
        text = _http_get(session, m3u8_url, headers).text
        segments, keys = _parse_m3u8(text, m3u8_url)

    if not segments:
        raise RuntimeError("No segments found in HLS playlist.")

    total = len(segments)
    prog.on_log(f"{total} segments. Downloading (resumable)…")

    # per-URL work dir so a restart finds the already-fetched .ts files
    work = os.path.join(outdir, ".cache_" + hashlib.md5(m3u8_url.encode()).hexdigest()[:12])
    os.makedirs(work, exist_ok=True)
    key_cache: dict[str, bytes] = {}

    completed = 0
    last_ui = 0.0
    for i, (seg_url, key) in enumerate(zip(segments, keys)):
        if prog.stop.is_set():
            prog.on_log("Stopped — segments kept on disk, restart to resume.")
            raise StopDownload()

        seg_path = os.path.join(work, f"seg_{i:06d}.ts")
        if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
            completed += 1
        else:
            data = _fetch_segment(session, seg_url, headers, key, i, key_cache)
            tmp = seg_path + ".part"
            with open(tmp, "wb") as f:        # streamed write, never the whole file in RAM
                f.write(data)
            os.replace(tmp, seg_path)
            completed += 1

        now = time.time()
        if now - last_ui > 0.1 or i == total - 1:
            pct = completed / total * 100
            prog.on_progress(pct, f"segment {completed}/{total}")
            last_ui = now

    out_path = os.path.join(outdir, _safe_name(title) + ".mp4")
    _mux_segments(work, total, out_path, prog)
    shutil.rmtree(work, ignore_errors=True)
    prog.on_log(f"Done: {out_path}")
    return out_path


def _fetch_segment(session, url, headers, key, index, key_cache) -> bytes:
    data = _http_get(session, url, headers).content
    if key:
        kbytes = key_cache.get(key["uri"])
        if kbytes is None:
            kbytes = _http_get(session, key["uri"], headers).content
            key_cache[key["uri"]] = kbytes
        iv = key["iv"] or index.to_bytes(16, "big")
        cipher = Cipher(algorithms.AES(kbytes), modes.CBC(iv))
        dec = cipher.decryptor()
        data = dec.update(data) + dec.finalize()
        # strip PKCS7 padding
        if data and data[-1] <= 16:
            data = data[: -data[-1]]
    return data


def _mux_segments(work: str, total: int, out_path: str, prog: Progress):
    """Concatenate .ts segments (streaming) then mux to mp4 with ffmpeg -c copy.

    Streaming concat keeps memory flat regardless of total size, and ffmpeg uses
    64-bit offsets so the resulting mp4 is fine well past 2 GB.
    """
    prog.on_progress(100.0, "joining segments…")
    big_ts = out_path + ".all.ts"
    with open(big_ts, "wb") as out:
        for i in range(total):
            seg = os.path.join(work, f"seg_{i:06d}.ts")
            if os.path.exists(seg):
                with open(seg, "rb") as f:
                    shutil.copyfileobj(f, out, length=1024 * 1024)
    prog.on_progress(100.0, "muxing to mp4 with ffmpeg…")
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", big_ts,
           "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", out_path]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        # fall back to a re-encode-free TS->MP4 without the audio bitstream filter
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", big_ts,
                        "-c", "copy", out_path], check=True)
    os.remove(big_ts)


# --------------------------------------------------------------------------- #
# GUI — browser-based, so it needs no native toolkit and runs anywhere on the
# uv-managed Python with just `uv sync` (the engine logic above is reused as-is).
# --------------------------------------------------------------------------- #
class GuiState:
    """Shared server-side state for the browser UI, mutated by worker threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.candidates: list[Candidate] = []
        self.thumbs: dict[int, Optional[bytes]] = {}
        self.outdir = DEFAULT_OUTDIR
        self.phase = "idle"   # idle|probing|choose|downloading|done|stopped|error
        self.progress = 0.0
        self.status = "idle"
        self.logs: list[str] = []
        self.final_path: Optional[str] = None
        self.error: Optional[str] = None

    def log(self, msg: str):
        with self.lock:
            self.logs.append(msg)

    def reset(self):
        self.stop_event.clear()
        self.candidates = []
        self.thumbs = {}
        self.phase = "idle"
        self.progress = 0.0
        self.status = ""
        self.logs = []
        self.final_path = None
        self.error = None


def _gui_start_download(state: GuiState, cand: Candidate):
    state.stop_event.clear()
    state.phase = "downloading"
    state.progress = 0.0
    state.status = "starting…"

    def on_progress(pct, status):
        state.progress = pct
        state.status = status

    prog = Progress(on_progress, state.log, state.stop_event)

    def work():
        try:
            path = download(cand, state.outdir or DEFAULT_OUTDIR, prog)
            state.final_path = path
            state.status = "finished ✓"
            state.phase = "done"
        except StopDownload:
            state.status = "stopped — click Download again to resume"
            state.phase = "stopped"
        except Exception as exc:
            state.error = str(exc)
            state.status = "error"
            state.phase = "error"

    state.thread = threading.Thread(target=work, daemon=True)
    state.thread.start()


def _gui_start_probe(state: GuiState, url: str):
    state.reset()
    state.phase = "probing"
    state.status = "probing…"
    state.log(f"Probing {url}")

    def work():
        try:
            cands = probe(url, state.log)
            state.candidates = cands
            if not cands:
                state.error = "No downloadable media found."
                state.phase = "error"
            elif len(cands) == 1:
                state.log(f"Found: {cands[0].label}")
                _gui_start_download(state, cands[0])
            else:
                state.log(f"{len(cands)} candidates found — choose one.")
                state.phase = "choose"
        except Exception as exc:
            state.error = str(exc)
            state.phase = "error"

    threading.Thread(target=work, daemon=True).start()


_GUI_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Allround Downloader</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#111}
 h1{font-size:20px} .row{display:flex;gap:8px;margin:8px 0}
 input[type=text]{flex:1;padding:8px;border:1px solid #ccc;border-radius:6px;font-size:14px}
 button{padding:8px 14px;border:0;border-radius:6px;background:#2563eb;color:#fff;cursor:pointer}
 button.sec{background:#e5e7eb;color:#111} button:disabled{opacity:.5;cursor:default}
 #bar{height:14px;background:#e5e7eb;border-radius:7px;overflow:hidden;margin:6px 0}
 #fill{height:100%;width:0;background:#2563eb;transition:width .2s}
 #pct{font-size:13px;color:#374151;min-height:18px}
 pre#log{background:#0b1020;color:#cbd5e1;padding:10px;border-radius:6px;height:220px;overflow:auto;font-size:12px;white-space:pre-wrap}
 .cand{display:flex;gap:10px;align-items:center;border:1px solid #e5e7eb;border-radius:8px;padding:8px;margin:6px 0}
 .cand img{width:120px;height:68px;object-fit:cover;background:#e5e7eb;border-radius:4px}
 .cand .lbl{flex:1;font-size:13px} #cands{margin:10px 0}
</style></head><body>
<h1>Allround Downloader</h1>
<div class="row">
 <input id="url" type="text" placeholder="Paste a video URL" value="__INITIAL_URL__">
 <button class="sec" onclick="paste()">Paste</button>
</div>
<div class="row">
 <input id="outdir" type="text" value="__OUTDIR__">
</div>
<div class="row">
 <button id="dl" onclick="start()">Download</button>
 <button id="stop" class="sec" onclick="stop()" disabled>Stop</button>
</div>
<div id="bar"><div id="fill"></div></div>
<div id="pct">idle</div>
<div id="cands"></div>
<pre id="log"></pre>
<script>
const $=id=>document.getElementById(id);
const T='__TOKEN__';
const q=p=>p+(p.includes('?')?'&':'?')+'t='+encodeURIComponent(T);
async function paste(){ try{ $('url').value=(await navigator.clipboard.readText()).trim(); }catch(e){} }
async function start(){
  const url=$('url').value.trim(); if(!url) return;
  $('cands').innerHTML='';
  await fetch(q('/probe'),{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({url, outdir:$('outdir').value.trim()})});
}
async function stop(){ await fetch(q('/stop'),{method:'POST'}); }
async function pick(i){ $('cands').innerHTML=''; await fetch(q('/select'),{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({index:i})}); }
let shownChoose=false;
function renderCands(cands){
  if(shownChoose) return; shownChoose=true;
  const box=$('cands'); box.textContent='';
  const h=document.createElement('b'); h.textContent='Choose the video to download:'; box.appendChild(h);
  cands.forEach(c=>{
    const d=document.createElement('div'); d.className='cand';
    const img=document.createElement('img'); img.loading='lazy';
    img.src=q('/thumb?index='+encodeURIComponent(c.index));
    const lbl=document.createElement('div'); lbl.className='lbl'; lbl.textContent=c.label;
    const btn=document.createElement('button'); btn.textContent='Download';
    btn.onclick=()=>pick(c.index);
    d.append(img,lbl,btn); box.appendChild(d);
  });
}
async function poll(){
  try{
    const s=await (await fetch(q('/status'))).json();
    $('fill').style.width=s.progress.toFixed(1)+'%';
    $('pct').textContent=s.progress.toFixed(1)+'%  '+s.status;
    $('log').textContent=s.logs; $('log').scrollTop=$('log').scrollHeight;
    $('dl').disabled=(s.phase==='probing'||s.phase==='downloading');
    $('stop').disabled=(s.phase!=='downloading');
    if(s.phase==='choose') renderCands(s.candidates); else if(s.phase!=='choose') shownChoose=false;
  }catch(e){}
  setTimeout(poll,400);
}
poll();
</script></body></html>"""


def run_gui(initial_url: str = ""):
    state = GuiState()
    # Per-process secret. The control server can start downloads and fetch
    # arbitrary URLs, so it must not be reachable by other pages/processes that
    # merely know the port. Every request must carry this token, and the Host
    # header must be loopback (defeats DNS-rebinding, where the browser sends an
    # attacker-controlled Host).
    token = secrets.token_urlsafe(32)

    def html() -> bytes:
        page = (_GUI_HTML
                .replace("__INITIAL_URL__", (initial_url or "").replace('"', "&quot;"))
                .replace("__OUTDIR__", DEFAULT_OUTDIR.replace('"', "&quot;"))
                .replace("__TOKEN__", token))
        return page.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence the default request logging
            pass

        def _send(self, code, ctype, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, "application/json", json.dumps(obj).encode("utf-8"))

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _authorized(self, qs: dict) -> bool:
            host = self.headers.get("Host", "").rsplit(":", 1)[0]
            if host not in ("127.0.0.1", "localhost"):
                return False
            return secrets.compare_digest(qs.get("t", [""])[0], token)

        def do_GET(self):
            parsed = urlparse(self.path)
            path, qs = parsed.path, parse_qs(parsed.query)
            if not self._authorized(qs):
                self._send(403, "text/plain", b"forbidden")
                return
            if path == "/" or path.startswith("/index"):
                self._send(200, "text/html; charset=utf-8", html())
            elif path == "/status":
                with state.lock:
                    self._json(200, {
                        "phase": state.phase,
                        "progress": state.progress,
                        "status": state.status,
                        "logs": "\n".join(state.logs[-300:]),
                        "candidates": [{"index": i, "label": c.label}
                                       for i, c in enumerate(state.candidates)],
                        "final_path": state.final_path,
                        "error": state.error,
                    })
            elif path == "/thumb":
                try:
                    i = int(qs.get("index", ["x"])[0])
                    if i not in state.thumbs:
                        state.thumbs[i] = make_thumbnail(state.candidates[i])
                    png = state.thumbs.get(i)
                except Exception:
                    png = None
                self._send(200, "image/png", png) if png else self._send(204, "image/png", b"")
            else:
                self._send(404, "text/plain", b"not found")

        def do_POST(self):
            parsed = urlparse(self.path)
            path, qs = parsed.path, parse_qs(parsed.query)
            if not self._authorized(qs):
                self._send(403, "text/plain", b"forbidden")
                return
            if path == "/probe":
                b = self._body()
                state.outdir = (b.get("outdir") or DEFAULT_OUTDIR).strip()
                url = (b.get("url") or "").strip()
                if url:
                    _gui_start_probe(state, url)
                self._json(200, {"ok": True})
            elif path == "/select":
                i = int(self._body().get("index", 0))
                if 0 <= i < len(state.candidates):
                    _gui_start_download(state, state.candidates[i])
                self._json(200, {"ok": True})
            elif path == "/stop":
                state.stop_event.set()
                self._json(200, {"ok": True})
            else:
                self._send(404, "text/plain", b"not found")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={token}"
    print(f"Allround Downloader UI: {url}", flush=True)
    print("(leave this running; press Ctrl+C to quit)", flush=True)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def run_cli(url: str, outdir: str, pick: Optional[int]):
    cands = probe(url)
    if not cands:
        print("No downloadable media found.")
        sys.exit(1)
    if len(cands) == 1:
        chosen = cands[0]
    elif pick is not None:
        chosen = cands[pick]
    else:
        print(f"{len(cands)} candidates found:")
        for i, c in enumerate(cands):
            print(f"  [{i}] {c.label}")
        print("Re-run with --pick <index> to download one.")
        return
    print(f"Downloading: {chosen.label}")

    def on_progress(pct, status):
        sys.stdout.write(f"\r{pct:6.2f}%  {status}        ")
        sys.stdout.flush()

    prog = Progress(on_progress, lambda m: print("\n" + m), threading.Event())
    path = download(chosen, outdir, prog)
    print(f"\nSaved: {path}")


def main():
    ap = argparse.ArgumentParser(description="Paste-the-URL allround video downloader.")
    ap.add_argument("url", nargs="?", help="video URL")
    ap.add_argument("--cli", action="store_true", help="headless download (no GUI)")
    ap.add_argument("--probe", action="store_true", help="list candidates and exit")
    ap.add_argument("--pick", type=int, help="candidate index to download in CLI mode")
    ap.add_argument("-o", "--outdir", default=DEFAULT_OUTDIR, help="output directory")
    args = ap.parse_args()

    if args.probe and args.url:
        for i, c in enumerate(probe(args.url)):
            print(f"[{i}] {c.label}  ->  {c.download_url}")
        return
    if args.cli and args.url:
        run_cli(args.url, args.outdir, args.pick)
        return
    run_gui(args.url or "")


if __name__ == "__main__":
    main()
