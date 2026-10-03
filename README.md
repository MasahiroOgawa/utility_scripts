# utility_scripts
This is a collection of utility shell scripts.

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

## `script/watermark_remover.py` — remove overlay watermarks

Removes semi-transparent light or dark text/logo watermarks, tiled or single. Every setting, including inputs and outputs, lives in a TOML config; the config path is the only argument.

```bash
uv run script/watermark_remover.py                      # uses script/watermark_remover.toml
uv run script/watermark_remover.py path/to/my.toml
```

`[io]` in the config:

- `input`: list of paths or glob patterns (`["photos/*.png", "a.jpg"]`), relative to the current directory.
- `output`: one output file; valid only when `input` matches a single image.
- `output_dir`: used when `output` is empty; each result keeps its input's file name (default `result/`).

An existing output prompts `Overwrite? [y/N/a]` (`a` = yes to all); non-interactive runs never overwrite.

How it works: if the watermark repeats, its lattice is found from the autocorrelation and the shifted copies are median-stacked, which cancels the image content and leaves a clean watermark template. The white strokes and their dark outline are then un-blended with a per-pixel opacity fitted across all copies, which recovers the underlying pixels rather than painting over them. Only what cannot be recovered goes to LaMa inpainting (ONNX, CPU; the ~200 MB model is downloaded to `~/.cache/watermark_remover/` on first use and checked by sha256). A single non-repeating watermark falls back to a stricter, light-only detection, which is noticeably less accurate.

`detect.stroke_kernel` must exceed the watermark's stroke width in pixels; raise it for high-resolution images.
