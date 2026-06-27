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


_PACK_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _unpack_packed_js(html: str) -> str:
    """Decode Dean-Edwards p.a.c.k.e.r'd scripts (used by missav/njav-family
    sites to hide the m3u8 URL). Returns the concatenated unpacked source."""

    def base_n(n: int, base: int) -> str:
        if n == 0:
            return _PACK_DIGITS[0]
        s = ""
        while n:
            s = _PACK_DIGITS[n % base] + s
            n //= base
        return s

    out = []
    for m in re.finditer(r"\}\('(.*?)',(\d+),(\d+),'(.*?)'\.split\('\|'\)", html, re.S):
        try:
            payload, base, count, words = (
                m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).split("|"))
            while count:
                count -= 1
                if count < len(words) and words[count]:
                    payload = re.sub(r"\b" + re.escape(base_n(count, base)) + r"\b",
                                     words[count], payload)
            out.append(payload)
        except Exception:
            continue
    return "\n".join(out)


def _page_title(html: str) -> Optional[str]:
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, re.I)
    if not m:
        m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    if not m:
        return None
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    # drop a trailing " - SiteName" / " | SiteName" suffix (separator must be
    # space-padded, so codes like "DASS-992" are left intact)
    return re.split(r"\s+[|\-–]\s+", title)[0].strip() or title


def _scrape_candidates(url: str, log: Callable[[str], None]) -> list[Candidate]:
    """Last-resort: pull .m3u8 / .mp4 URLs out of the page HTML, including ones
    hidden inside packed/obfuscated JS (missav/njav embed them that way)."""
    headers = {"User-Agent": USER_AGENT, "Referer": url}
    try:
        html = make_session().get(url, headers=headers, timeout=30).text
    except Exception as exc:
        log(f"Could not fetch page: {exc}")
        return []

    # search the raw HTML *and* any unpacked JS for media URLs
    text = html + "\n" + _unpack_packed_js(html)
    found: list[str] = []
    for m in re.finditer(r'https?://[^\s"\'<>\\]+?\.(?:m3u8|mp4)[^\s"\'<>\\]*', text):
        u = m.group(0)
        if u not in found:
            found.append(u)
    # also catch escaped JSON urls like https:\/\/...
    for m in re.finditer(r'https?:\\?/\\?/[^\s"\'<>]+?\.(?:m3u8|mp4)', text):
        u = m.group(0).replace("\\/", "/")
        if u not in found:
            found.append(u)

    # If a master HLS playlist is present, prefer it: it auto-selects the best
    # resolution, so we drop the per-resolution variants and preview clips.
    masters = [u for u in found if re.search(r"/playlist\.m3u8", u)]
    if masters:
        found = masters

    log(f"Scraped {len(found)} media URL(s) from page.")
    page_title = _page_title(html)
    cands = []
    for u in found:
        ext = "m3u8" if ".m3u8" in u else "mp4"
        cands.append(
            Candidate(
                title=page_title or os.path.basename(urlparse(u).path) or ext,
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
def _http_get(session: requests.Session, url: str, headers: dict,
              retries: int = 5, timeout: int = 120, stop=None, **kw) -> requests.Response:
    """GET with retries + backoff so a slow/flaky CDN segment doesn't kill the job."""
    last = None
    for attempt in range(retries):
        if stop is not None and stop.is_set():
            raise StopDownload()
        try:
            r = session.get(url, headers=headers, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 10))
    raise last


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
            data = _fetch_segment(session, seg_url, headers, key, i, key_cache, prog.stop)
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


def _fetch_segment(session, url, headers, key, index, key_cache, stop=None) -> bytes:
    data = _http_get(session, url, headers, stop=stop).content
    if key:
        kbytes = key_cache.get(key["uri"])
        if kbytes is None:
            kbytes = _http_get(session, key["uri"], headers, stop=stop).content
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
MAX_PARALLEL = 4   # most downloads running at once; extras queue and wait


class Job:
    """One download. Up to MAX_PARALLEL run concurrently; the rest queue."""

    def __init__(self, jid: int, cand: Candidate):
        self.id = jid
        self.title = cand.title or f"job {jid}"
        self.cand = cand
        self.stop_event = threading.Event()
        self.phase = "queued"   # queued|downloading|done|stopped|error
        self.progress = 0.0
        self.status = "queued"
        self.final_path: Optional[str] = None
        self.error: Optional[str] = None


class GuiState:
    """Shared server-side state for the browser UI, mutated by worker threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.sem = threading.Semaphore(MAX_PARALLEL)
        self.jobs: dict[int, Job] = {}
        self.order: list[int] = []
        self.next_id = 1
        self.candidates: list[Candidate] = []
        self.thumbs: dict[int, Optional[bytes]] = {}
        self.outdir = DEFAULT_OUTDIR
        self.probe_phase = "idle"   # idle|probing|choose|error
        self.probe_status = "idle"
        self.probe_error: Optional[str] = None
        self.logs: list[str] = []

    def log(self, msg: str):
        with self.lock:
            self.logs.append(msg)


def _run_job(state: GuiState, job: Job):
    """(Re)start a job's download on its own thread. Re-runnable: a download that
    stopped or errored resumes from disk (HLS skips cached segments; yt-dlp
    continues the partial file)."""

    def on_progress(pct, status):
        job.progress = pct
        job.status = status

    def jlog(msg):
        state.log(f"[{job.title[:28]}] {msg}")

    def work():
        job.phase = "queued"
        job.status = "queued — waiting for a free slot"
        state.sem.acquire()
        try:
            if job.stop_event.is_set():        # stopped while still queued
                job.phase, job.status = "stopped", "stopped"
                return
            job.phase, job.status = "downloading", "starting…"
            job.error = None
            prog = Progress(on_progress, jlog, job.stop_event)
            job.final_path = download(job.cand, state.outdir or DEFAULT_OUTDIR, prog)
            job.progress, job.phase, job.status = 100.0, "done", "finished ✓"
        except StopDownload:
            job.phase, job.status = "stopped", "stopped — Resume to continue"
        except Exception as exc:
            job.error, job.phase, job.status = str(exc), "error", f"error: {exc}"
        finally:
            state.sem.release()

    threading.Thread(target=work, daemon=True).start()


def _gui_start_job(state: GuiState, cand: Candidate) -> Job:
    with state.lock:
        jid = state.next_id
        state.next_id += 1
        job = Job(jid, cand)
        state.jobs[jid] = job
        state.order.append(jid)
    _run_job(state, job)
    return job


def _gui_resume_job(state: GuiState, jid: int):
    """Re-run a stopped/errored/finished job; resumes from whatever is on disk."""
    job = state.jobs.get(jid)
    if job and job.phase in ("stopped", "error", "done"):
        job.stop_event.clear()
        _run_job(state, job)


def _gui_start_probe(state: GuiState, url: str):
    with state.lock:
        state.candidates = []
        state.thumbs = {}
    state.probe_phase = "probing"
    state.probe_status = "probing…"
    state.probe_error = None
    state.log(f"Probing {url}")

    def work():
        try:
            cands = probe(url, state.log)
            with state.lock:
                state.candidates = cands
            if not cands:
                state.probe_phase, state.probe_error = "error", "No downloadable media found."
            elif len(cands) == 1:
                state.log(f"Found: {cands[0].label}")
                state.probe_phase = "idle"
                _gui_start_job(state, cands[0])
            else:
                state.log(f"{len(cands)} candidates — pick any (up to "
                          f"{MAX_PARALLEL} download at once).")
                state.probe_phase = "choose"
        except Exception as exc:
            state.probe_phase, state.probe_error = "error", str(exc)

    threading.Thread(target=work, daemon=True).start()


_GUI_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Allround Downloader</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#111}
 h1{font-size:20px} .row{display:flex;gap:8px;margin:8px 0}
 input[type=text]{flex:1;padding:8px;border:1px solid #ccc;border-radius:6px;font-size:14px}
 button{padding:8px 14px;border:0;border-radius:6px;background:#2563eb;color:#fff;cursor:pointer}
 button.sec{background:#e5e7eb;color:#111} button:disabled{opacity:.5;cursor:default}
 pre#log{background:#0b1020;color:#cbd5e1;padding:10px;border-radius:6px;height:180px;overflow:auto;font-size:12px;white-space:pre-wrap}
 .cand{display:flex;gap:10px;align-items:center;border:1px solid #e5e7eb;border-radius:8px;padding:8px;margin:6px 0}
 .cand img{width:120px;height:68px;object-fit:cover;background:#e5e7eb;border-radius:4px}
 .cand .lbl{flex:1;font-size:13px} #cands,#jobs{margin:10px 0}
 .job{border:1px solid #e5e7eb;border-radius:8px;padding:8px 10px;margin:6px 0}
 .job .top{display:flex;gap:8px;align-items:center}
 .job .ttl{flex:1;font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .bar{height:12px;background:#e5e7eb;border-radius:6px;overflow:hidden;margin:6px 0 2px}
 .fill{height:100%;width:0;background:#2563eb;transition:width .2s}
 .fill.done{background:#16a34a} .fill.error{background:#dc2626} .fill.stopped{background:#9ca3af}
 .job .st{font-size:12px;color:#374151;min-height:16px}
 .hint{font-size:12px;color:#6b7280}
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
 <span class="hint" id="hint"></span>
</div>
<div id="cands"></div>
<div id="jobs"></div>
<pre id="log"></pre>
<script>
const $=id=>document.getElementById(id);
const T='__TOKEN__';
const q=p=>p+(p.includes('?')?'&':'?')+'t='+encodeURIComponent(T);
async function paste(){ try{ $('url').value=(await navigator.clipboard.readText()).trim(); }catch(e){} }
async function start(){
  const url=$('url').value.trim(); if(!url) return;
  await fetch(q('/probe'),{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({url, outdir:$('outdir').value.trim()})});
}
async function pick(i){ await fetch(q('/select'),{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({index:i})}); }
async function stop(id){ await fetch(q('/stop'),{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({id})}); }
async function resume(id){ await fetch(q('/resume'),{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({id})}); }
let shownChoose=false;
function renderCands(cands){
  if(shownChoose) return; shownChoose=true;
  const box=$('cands'); box.textContent='';
  const h=document.createElement('b');
  h.textContent='Choose video(s) to download — click several to queue them:';
  box.appendChild(h);
  cands.forEach(c=>{
    const d=document.createElement('div'); d.className='cand';
    const img=document.createElement('img'); img.loading='lazy';
    img.src=q('/thumb?index='+encodeURIComponent(c.index));
    const lbl=document.createElement('div'); lbl.className='lbl'; lbl.textContent=c.label;
    const btn=document.createElement('button'); btn.textContent='Download';
    btn.onclick=()=>{ pick(c.index); btn.textContent='Queued ✓'; };
    d.append(img,lbl,btn); box.appendChild(d);
  });
}
function renderJobs(jobs){
  const box=$('jobs'); box.textContent='';
  jobs.forEach(j=>{
    const d=document.createElement('div'); d.className='job';
    const top=document.createElement('div'); top.className='top';
    const t=document.createElement('div'); t.className='ttl'; t.textContent='#'+j.id+'  '+j.title;
    top.appendChild(t);
    if(j.phase==='queued'||j.phase==='downloading'){
      const b=document.createElement('button'); b.className='sec'; b.textContent='Stop';
      b.onclick=()=>stop(j.id); top.appendChild(b);
    } else if(j.phase==='stopped'||j.phase==='error'){
      const b=document.createElement('button'); b.textContent='Resume';
      b.onclick=()=>resume(j.id); top.appendChild(b);
    }
    const bar=document.createElement('div'); bar.className='bar';
    const fill=document.createElement('div'); fill.className='fill '+j.phase;
    fill.style.width=j.progress.toFixed(1)+'%'; bar.appendChild(fill);
    const st=document.createElement('div'); st.className='st';
    st.textContent=j.progress.toFixed(1)+'%  '+j.status;
    d.append(top,bar,st); box.appendChild(d);
  });
}
async function poll(){
  try{
    const s=await (await fetch(q('/status'))).json();
    $('dl').disabled=(s.probe_phase==='probing');
    $('hint').textContent=(s.probe_phase==='probing'?'probing…':
      (s.probe_error?('error: '+s.probe_error):
       (s.running+' of '+s.max_parallel+' slots busy'+(s.queued?(', '+s.queued+' queued'):''))));
    $('log').textContent=s.logs; $('log').scrollTop=$('log').scrollHeight;
    renderJobs(s.jobs);
    if(s.probe_phase==='choose') renderCands(s.candidates);
    else if(s.probe_phase!=='choose'){ shownChoose=false; $('cands').textContent=''; }
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
                    jobs = [state.jobs[j] for j in state.order]
                    running = sum(1 for j in jobs if j.phase == "downloading")
                    queued = sum(1 for j in jobs if j.phase == "queued")
                    self._json(200, {
                        "probe_phase": state.probe_phase,
                        "probe_status": state.probe_status,
                        "probe_error": state.probe_error,
                        "max_parallel": MAX_PARALLEL,
                        "running": running,
                        "queued": queued,
                        "logs": "\n".join(state.logs[-300:]),
                        "candidates": [{"index": i, "label": c.label}
                                       for i, c in enumerate(state.candidates)],
                        "jobs": [{"id": j.id, "title": j.title, "phase": j.phase,
                                  "progress": j.progress, "status": j.status}
                                 for j in jobs],
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
                    _gui_start_job(state, state.candidates[i])
                self._json(200, {"ok": True})
            elif path == "/stop":
                jid = self._body().get("id")
                job = state.jobs.get(jid)
                if job:
                    job.stop_event.set()
                self._json(200, {"ok": True})
            elif path == "/resume":
                _gui_resume_job(state, self._body().get("id"))
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
