# iPhone ↔ Ubuntu file transfer

Two complementary scripts under `script/`. Both write files into `/home/mas/iphone-share/` — create the directory (or edit `DEST_DIR` / `SHARE_DIR` in the scripts) before first use.

## `cp_iphone_app_docs.sh` — pull an iPhone app's Documents over USB

Mounts a specific iOS app's Documents folder via `ifuse` and copies everything into `/home/mas/iphone-share/`. Use this for large captures (multi-GB) — USB is the only reliable path.

Prereqs (one-time):

```bash
sudo apt install -y ifuse libimobiledevice-utils ideviceinstaller
```

Run:

1. Plug iPhone in, unlock, tap **Trust** on the first-time prompt.
2. `./script/cp_iphone_app_docs.sh`

The script lists the app's Documents, copies into `/home/mas/iphone-share/`, then unmounts and cleans up `/tmp/iphone-app-docs` via an `EXIT` trap. The bundle ID is hard-coded (`com.dopymas.dopescan.3SM82K4JRQ`); edit `BUNDLE_ID` to target a different app. To discover bundle IDs: `ideviceinstaller -l`.

## `launch_upload_server.sh` — small-file drop over LAN

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
