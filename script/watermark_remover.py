#!/usr/bin/env python3
"""Remove semi-transparent overlay watermarks (text / logos, tiled or single).

Pipeline:
  1. detect   morphological top-hat (light) or black-hat (dark) on Lab L
  2. lattice  if the watermark tiles, find the lattice from the autocorrelation
              and median-stack shifted copies: image content averages out and
              only the watermark survives
  3. unblend  model I = a*c + (1-a)*J with c white for the strokes and black
              for their outline / shadow; fit opacity a per pixel, invert for J
  4. inpaint  pixels too opaque to recover go to LaMa (ONNX), tile by tile

Usage:
  uv run script/watermark_remover.py [config.toml]

Inputs, outputs and every tuning knob live in the config (default:
script/watermark_remover.toml). An existing output prompts before overwrite.
"""

import argparse
import glob
import hashlib
import sys
import tomllib
from pathlib import Path

import cv2
import numpy as np
import requests

DEFAULT_CONFIG = Path(__file__).with_suffix(".toml")

MODELS = {
    "lama_fp32": {
        "url": "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx",
        "sha256": "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6",
        "size": 512,
    },
}

# Row band height for shift-stacking; bounds memory to band * width * copies.
BAND = 64


# ---- model registry ---------------------------------------------------------
def resolve_model(name: str, cache_dir: str) -> tuple[str, Path]:
    if name not in MODELS:
        sys.exit(f"unknown inpaint model {name!r}; known: {', '.join(MODELS)}")
    spec = MODELS[name]
    path = Path(cache_dir).expanduser() / Path(spec["url"]).name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {name} -> {path}")
        part = path.with_suffix(".part")
        with requests.get(spec["url"], stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(part, "wb") as f:
                f.writelines(r.iter_content(1 << 20))
        part.rename(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != spec["sha256"]:
        sys.exit(f"{path}: sha256 mismatch (expected {spec['sha256']}); delete it and retry")
    return name, path


class Inpainter:
    def __init__(self, cfg: dict):
        import onnxruntime as ort

        self.name, path = resolve_model(cfg["model"], cfg["cache_dir"])
        self.size = MODELS[self.name]["size"]
        self.tile, self.overlap, self.feather = cfg["tile"], cfg["overlap"], cfg["feather"]
        if self.tile != self.size:
            sys.exit(f"inpaint.tile={self.tile} but model {self.name} is fixed at {self.size}")
        self.sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])

    def _run(self, rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
        img = rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        m = (mask > 0).astype(np.float32)[None, None]
        out = self.sess.run(None, {"image": img, "mask": m})[0][0]
        return out.transpose(1, 2, 0)

    def __call__(self, bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if not mask.any():
            return bgr
        h, w = mask.shape
        t = self.tile
        ph, pw = max(0, t - h), max(0, t - w)
        rgb = cv2.copyMakeBorder(bgr[..., ::-1], 0, ph, 0, pw, cv2.BORDER_REFLECT_101)
        m = cv2.copyMakeBorder(mask, 0, ph, 0, pw, cv2.BORDER_CONSTANT, value=0)
        H, W = m.shape
        step = t - self.overlap
        starts = lambda n: sorted({*range(0, n - t, step), n - t})
        ramp = np.minimum(np.arange(t) + 1, np.arange(t)[::-1] + 1)
        ramp = np.minimum(ramp, max(self.overlap, 1)).astype(np.float32)
        weight = np.outer(ramp, ramp)[..., None]
        acc = np.zeros((H, W, 3), np.float32)
        wsum = np.zeros((H, W, 1), np.float32)
        tiles = [(y, x) for y in starts(H) for x in starts(W) if m[y : y + t, x : x + t].any()]
        for y, x in tiles:
            acc[y : y + t, x : x + t] += self._run(rgb[y : y + t, x : x + t], m[y : y + t, x : x + t]) * weight
            wsum[y : y + t, x : x + t] += weight
        filled = (acc / np.maximum(wsum, 1e-6))[:h, :w, ::-1]
        soft = cv2.GaussianBlur(
            cv2.dilate(mask, None, iterations=self.feather).astype(np.float32), (0, 0), max(self.feather, 1) / 2
        )
        soft = np.maximum(soft, (mask > 0).astype(np.float32))[..., None]
        return bgr * (1 - soft) + filled * soft


# ---- detection --------------------------------------------------------------
def response(L: np.ndarray, polarity: str, kernel: int) -> np.ndarray:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel))
    op = cv2.MORPH_TOPHAT if polarity == "light" else cv2.MORPH_BLACKHAT
    return cv2.morphologyEx(L, op, k)


def robust_threshold(x: np.ndarray, k: float) -> float:
    med = float(np.median(x))
    return med + k * 1.4826 * float(np.median(np.abs(x - med))) + 1e-6


def autocorrelation(x: np.ndarray) -> np.ndarray:
    h, w = x.shape
    P = np.zeros((2 * h, 2 * w), np.float32)
    P[:h, :w] = x - x.mean()
    F = np.fft.rfft2(P)
    ac = np.fft.irfft2(F * np.conj(F), s=P.shape)
    return np.fft.fftshift(ac / ac[0, 0]).astype(np.float32)


def peak_near(ac: np.ndarray, v: np.ndarray, r: int = 3) -> tuple[np.ndarray, float]:
    """Sub-pixel local maximum of `ac` around lattice offset `v` (dy, dx)."""
    cy, cx = ac.shape[0] // 2, ac.shape[1] // 2
    y0, x0 = int(round(cy + v[0])), int(round(cx + v[1]))
    if not (r + 1 <= y0 < ac.shape[0] - r - 1 and r + 1 <= x0 < ac.shape[1] - r - 1):
        return v, 0.0
    win = ac[y0 - r : y0 + r + 1, x0 - r : x0 + r + 1]
    dy, dx = np.unravel_index(win.argmax(), win.shape)
    y, x = y0 - r + dy, x0 - r + dx
    sub = lambda a, b, c: 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) < 0 else 0.0
    oy = sub(ac[y - 1, x], ac[y, x], ac[y + 1, x])
    ox = sub(ac[y, x - 1], ac[y, x], ac[y, x + 1])
    return np.array([y - cy + oy, x - cx + ox]), float(ac[y, x])


def find_lattice(resp: np.ndarray, cfg: dict) -> tuple[np.ndarray, float] | None:
    """Return (basis 2x2 rows=(dy,dx), score) of the watermark tiling, or None."""
    ac = autocorrelation(resp)
    cy, cx = ac.shape[0] // 2, ac.shape[1] // 2
    yy, xx = np.mgrid[: ac.shape[0], : ac.shape[1]]
    half = (yy > cy) | ((yy == cy) & (xx > cx))
    is_max = (ac == cv2.dilate(ac, np.ones((5, 5), np.uint8))) & half
    is_max &= np.hypot(yy - cy, xx - cx) >= cfg["min_period"]
    is_max &= ac >= cfg["min_score"] / 2
    ys, xs = np.nonzero(is_max)
    order = np.argsort(-ac[ys, xs])[:30]
    cands = [np.array([ys[i] - cy, xs[i] - cx], float) for i in order]

    best = None
    for i, v1 in enumerate(cands[:6]):
        for v2 in cands[i + 1 :]:
            sin = abs(v1[0] * v2[1] - v1[1] * v2[0]) / (np.linalg.norm(v1) * np.linalg.norm(v2))
            if sin < 0.34:
                continue
            # A true lattice also peaks at the combinations, not just the two seeds.
            score = np.mean([peak_near(ac, c)[1] for c in (v1, v2, v1 + v2, v1 - v2, 2 * v1, 2 * v2)])
            if best is None or score > best[1]:
                best = (np.stack([v1, v2]), score)
    if best is None or best[1] < cfg["min_score"]:
        return None

    basis, score = best
    h, w = resp.shape
    ij, pos = [], []
    for i in range(-6, 7):
        for j in range(-6, 7):
            if (i, j) == (0, 0):
                continue
            v = i * basis[0] + j * basis[1]
            if abs(v[0]) > 0.8 * h or abs(v[1]) > 0.8 * w:
                continue
            p, val = peak_near(ac, v)
            if val >= cfg["min_score"] / 2:
                ij.append((i, j))
                pos.append(p)
    if len(ij) >= 4:
        basis = np.linalg.lstsq(np.array(ij, float), np.array(pos), rcond=None)[0]
    return basis, float(score)


def lattice_shifts(basis: np.ndarray, shape: tuple[int, int], max_copies: int) -> list[np.ndarray]:
    h, w = shape
    vs = [i * basis[0] + j * basis[1] for i in range(-20, 21) for j in range(-20, 21)]
    vs = [v for v in vs if abs(v[0]) < 0.8 * h and abs(v[1]) < 0.8 * w]
    return sorted(vs, key=np.linalg.norm)[:max_copies]


def shifted_band(arr: np.ndarray, shifts: list[np.ndarray], y0: int, y1: int) -> np.ndarray:
    """Stack of arr(p + s) for p in rows y0:y1; NaN where p + s is outside the image."""
    w = arr.shape[1]
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(y0, y1, dtype=np.float32))
    return np.stack(
        [
            cv2.remap(
                arr,
                gx + np.float32(s[1]),
                gy + np.float32(s[0]),
                cv2.INTER_LINEAR,
                # A scalar border value fills only channel 0.
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(np.nan,) * 4,
            )
            for s in shifts
        ]
    )


def stack_median(arr: np.ndarray, shifts: list[np.ndarray]) -> np.ndarray:
    h = arr.shape[0]
    return np.concatenate(
        [np.nanmedian(shifted_band(arr, shifts, y, min(y + BAND, h)), axis=0) for y in range(0, h, BAND)]
    )


def clean_mask(m: np.ndarray, min_component: int) -> np.ndarray:
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_component
    return keep[lab]


# ---- opacity / colour -------------------------------------------------------
def fit_alpha(I: np.ndarray, B: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Least-squares a in I - B = a (c - B), pooled over channels (last axis)."""
    d = c - B
    return np.sum((I - B) * d, axis=-1) / np.maximum(np.sum(d * d, axis=-1), 1e-3)


def stack_alpha(I: np.ndarray, B: np.ndarray, cmap: np.ndarray, shifts) -> np.ndarray:
    """fit_alpha pooled over lattice copies too.

    Least squares weights each copy by its contrast (c - B)^2, so copies on
    backgrounds close to the overlay colour, where a is unobservable, barely count.
    """
    h = I.shape[0]
    out = []
    for y in range(0, h, BAND):
        y1 = min(y + BAND, h)
        Is, Bs = shifted_band(I, shifts, y, y1), shifted_band(B, shifts, y, y1)
        d = cmap[y:y1] - Bs
        out.append(np.nansum((Is - Bs) * d, axis=(0, 3)) / np.maximum(np.nansum(d * d, axis=(0, 3)), 1e-3))
    return np.concatenate(out)


# ---- main per-image routine -------------------------------------------------
def remove_watermark(bgr: np.ndarray, cfg: dict, inpaint: Inpainter) -> tuple[np.ndarray, str]:
    """Return the cleaned image and a one-line description of what was found.

    The overlay is modelled in two layers: the primary strokes (white for a
    light watermark) and an opposite-signed outline / drop shadow (black),
    each blended with a per-pixel opacity.
    """
    dc, lc, uc, rc = cfg["detect"], cfg["lattice"], cfg["unblend"], cfg["residual"]
    if dc["polarity"] not in ("auto", "light", "dark"):
        sys.exit(f"detect.polarity must be auto|light|dark, got {dc['polarity']!r}")
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    L = lab[..., 0].astype(np.float32)
    chroma = np.hypot(lab[..., 1].astype(np.float32) - 128, lab[..., 2].astype(np.float32) - 128)

    cands = []
    for pol in ["light", "dark"] if dc["polarity"] == "auto" else [dc["polarity"]]:
        r = response(L, pol, dc["stroke_kernel"])
        lat = find_lattice(r, lc) if lc["enabled"] else None
        cands.append((lat[1] if lat else 0.0, pol, r, lat))
    # Without a lattice nothing separates a dark overlay from dark image detail
    # (eyes, hair), so auto then assumes the common case: a light watermark.
    _, pol, resp, lat = max(cands, key=lambda c: (c[0], c[1] == "light"))
    assumed = dc["polarity"] == "auto" and lat is None
    sign = 1.0 if pol == "light" else -1.0

    # Signed high-pass of L: positive on light strokes, negative on dark ones.
    hp = sign * (L - cv2.medianBlur(lab[..., 0], 4 * dc["stroke_kernel"] + 1).astype(np.float32))
    if lat:
        basis, score = lat
        shifts = lattice_shifts(basis, L.shape, lc["max_copies"])
        T = stack_median(hp, shifts)
        thr = robust_threshold(np.abs(T), lc["threshold_k"])
        primary, secondary = T > thr, T < -thr
        desc = (
            f"polarity={pol} lattice=({basis[0][0]:.1f},{basis[0][1]:.1f})"
            f"/({basis[1][0]:.1f},{basis[1][1]:.1f}) score={score:.2f} copies={len(shifts)}"
        )
    else:
        # Single layer: every masked pixel blends toward the primary colour.
        shifts, T = None, np.ones_like(hp)
        primary = (resp > robust_threshold(resp, dc["threshold_k"])) & (chroma < dc["max_chroma"])
        # An outline layer is only identifiable by its repetition; untiled, dark
        # pixels beside highlights (pupils next to catchlights) would match it.
        secondary = np.zeros_like(primary)
        desc = f"polarity={pol}{' (assumed)' if assumed else ''} lattice=none"
    primary = clean_mask(primary, dc["min_component"])
    secondary = clean_mask(secondary, dc["min_component"]) & ~primary
    core = primary | secondary
    region = cv2.dilate(core.astype(np.uint8), None, iterations=1) > 0

    I = bgr.astype(np.float32)
    bg_mask = cv2.dilate(region.astype(np.uint8), None, iterations=uc["background_dilate"])
    B = cv2.inpaint(bgr, bg_mask, 5, cv2.INPAINT_TELEA).astype(np.float32)

    # Pure white / black: a colour fitted across copies is biased by the noise
    # in B (errors-in-variables), while white/black fits all channels consistently.
    light, dark = np.full(3, 255.0, np.float32), np.zeros(3, np.float32)
    cmap = np.where((T > 0)[..., None], *((light, dark) if pol == "light" else (dark, light)))

    # Opacity is a property of the watermark, so a tiled one is fitted across copies.
    alpha = stack_alpha(I, B, cmap, shifts) if shifts else fit_alpha(I, B, cmap)
    alpha = np.clip(np.where(region, alpha, 0), 0, 1)
    a = np.minimum(alpha, uc["alpha_max"])[..., None]
    J = np.clip((I - a * cmap) / (1 - a), 0, 255)

    lab_j = cv2.cvtColor(J.astype(np.uint8), cv2.COLOR_BGR2LAB)[..., 0].astype(np.float32)
    left = response(lab_j, pol, dc["stroke_kernel"]) > robust_threshold(resp, rc["threshold_k"])
    residual = (alpha > uc["alpha_max"]) | (primary & left)
    residual = cv2.dilate(residual.astype(np.uint8), None, iterations=rc["dilate"])

    out = inpaint(J, residual)
    desc += f" mask={region.mean():.1%} inpainted={residual.mean():.1%}"
    return np.clip(out + 0.5, 0, 255).astype(np.uint8), desc


# ---- CLI --------------------------------------------------------------------
def confirm_overwrite(path: Path, state: dict) -> bool:
    if not path.exists() or state.get("all"):
        return True
    if not sys.stdin.isatty():
        print(f"skip {path}: exists (non-interactive, not overwriting)")
        return False
    ans = input(f"Overwrite {path}? [y/N/a] ").strip().lower()
    state["all"] = ans == "a"
    return ans in ("y", "a")


def resolve_inputs(patterns: list[str]) -> list[Path]:
    found: dict[Path, None] = {}
    for pat in patterns:
        hits = sorted(glob.glob(str(Path(pat).expanduser()), recursive=True))
        if not hits:
            print(f"warning: io.input {pat!r} matched nothing")
        found.update({Path(h): None for h in hits if Path(h).is_file()})
    return list(found)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"TOML config (default: {DEFAULT_CONFIG.name} beside this script)",
    )
    args = ap.parse_args()

    cfg = tomllib.loads(args.config.read_text())
    io = cfg["io"]
    inputs = resolve_inputs(io["input"])
    if not inputs:
        sys.exit("io.input matched no files")
    output = Path(io["output"]).expanduser() if io.get("output") else None
    if output and len(inputs) > 1:
        sys.exit(
            f"io.output names one file but io.input matched {len(inputs)}; "
            "leave io.output empty to write each result into io.output_dir"
        )
    inpaint = Inpainter(cfg["inpaint"])
    print(f"config: {args.config}  inpaint model: {inpaint.name}  inputs: {len(inputs)}")

    state: dict = {}
    for src in inputs:
        dst = output or Path(io["output_dir"]).expanduser() / src.name
        if dst.resolve() == src.resolve():
            print(f"skip {src}: output would overwrite the input")
            continue
        if not confirm_overwrite(dst, state):
            continue
        bgr = cv2.imread(str(src), cv2.IMREAD_COLOR)
        if bgr is None:
            print(f"skip {src}: cannot read image")
            continue
        out, desc = remove_watermark(bgr, cfg, inpaint)
        dst.parent.mkdir(parents=True, exist_ok=True)
        params = [cv2.IMWRITE_JPEG_QUALITY, io["jpeg_quality"]] if dst.suffix.lower() in (".jpg", ".jpeg") else []
        if not cv2.imwrite(str(dst), out, params):
            sys.exit(f"cannot write {dst}")
        print(f"{src} -> {dst}  {desc}")


if __name__ == "__main__":
    main()
