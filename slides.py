#!/usr/bin/env python3
"""YouTube video -> slide screenshots -> PDF.

Non-interactive (safe for scripts/agents). For the interactive flow use ./run.sh.

  python slides.py extract-images <youtube-url> --name <name>   # step 1: saves unique slides
  python slides.py generate-pdf <session-folder>    # step 2: builds PDF from the (reviewed) images
"""
import argparse
import re
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import yt_dlp
from PIL import Image

SESSIONS_DIR = Path("sessions")
COMPARE_SIZE = (320, 180)


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "session"


def progress(label, fraction, detail=""):
    """Render a single self-updating line: `Label [####----]  42%  detail`."""
    pct = max(0, min(100, int(fraction * 100)))
    bar = "#" * (pct // 4) + "-" * (25 - pct // 4)
    print(f"\r\033[K{label:<12}[{bar}] {pct:3d}%  {detail}", end="", flush=True)


def cli_report(stage, fraction, detail=""):
    """Default progress reporter for the terminal. fraction=None means unknown length."""
    if fraction is None:
        print(f"\r\033[K{stage}... {detail}", end="", flush=True)
    else:
        progress(stage, fraction, detail)
        if fraction >= 1:
            print()


def cli_log(msg):
    print(f"\r\033[K{msg}", flush=True)


def clock(seconds):
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def download_video(url, max_height, dest_dir, verbose=False, report=cli_report, log=cli_log):
    """Download a video-only file (no ffmpeg needed) with a live progress line."""
    t0 = time.time()

    class Log:
        """Timestamped yt-dlp log lines, printed above the live progress line."""
        def _emit(self, msg):
            log(f"[{time.time() - t0:6.1f}s] {msg}")

        def debug(self, msg):
            if verbose and not msg.startswith("[debug] "):
                self._emit(msg)
            elif verbose:
                self._emit(msg[8:])

        def info(self, msg):
            self._emit(msg)

        def warning(self, msg):
            self._emit(f"WARNING: {msg}")

        def error(self, msg):
            self._emit(f"ERROR: {msg}")

    fetching = threading.Event()  # set once the first download update arrives

    def tick():
        start = time.time()
        while not fetching.wait(0.5):
            report("Fetching info", None, f"{int(time.time() - start)}s elapsed")

    def hook(d):
        fetching.set()
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes", 0)
            speed = d.get("speed")
            detail = f"{done / 1e6:.1f}/{total / 1e6:.1f} MB"
            if speed:
                detail += f"  {speed / 1e6:.1f} MB/s"
            report("Downloading", done / total if total else 0, detail)
        elif d["status"] == "finished":
            report("Downloading", 1, "done")

    h = max_height
    opts = {"logger": Log(), "verbose": verbose, "noprogress": True, "progress_hooks": [hook],
            "socket_timeout": 30,
            # Force IPv4: on some networks IPv6 to YouTube hangs until it times out.
            "source_address": "0.0.0.0",
            # YouTube needs a JS runtime to solve its player challenges; deno is the
            # default, so also allow node/bun if installed.
            "js_runtimes": {"deno": {}, "node": {}, "bun": {}},
            "outtmpl": str(Path(dest_dir) / "video.%(ext)s"),
            # H.264 first: OpenCV's bundled decoder handles it; VP9/AV1 may not decode
            "format": f"bestvideo[height<={h}][vcodec^=avc1]/bestvideo[height<={h}][ext=mp4]/best[height<={h}][ext=mp4]"}
    threading.Thread(target=tick, daemon=True).start()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info), info.get("title", "")
    finally:
        fetching.set()


def fingerprint(frame):
    small = cv2.resize(frame, COMPARE_SIZE, interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)


PIXEL_DELTA = 25  # a pixel counts as changed when its gray level moves by more than this


def difference(a, b):
    """Percentage (0-100) of pixels that changed noticeably between two fingerprints.

    Counting changed pixels, rather than averaging the change, keeps small edits
    (a new bullet line) visible on slides that are mostly blank.
    """
    return float(np.mean(np.abs(a - b) > PIXEL_DELTA) * 100)


def fmt_time(seconds):
    s = int(seconds)
    return f"{s // 3600:02d}h{s % 3600 // 60:02d}m{s % 60:02d}s"


def new_session_folder(name):
    folder = SESSIONS_DIR / f"{slugify(name)}-{datetime.now():%Y%m%d-%H%M%S}"
    folder.mkdir(parents=True)
    return folder


def extract_images(args):
    name = args.name.strip()
    if not name:
        sys.exit("--name must not be empty.")
    folder = new_session_folder(name)

    print(f"Session folder: {folder}")
    with tempfile.TemporaryDirectory() as tmp:
        video, title = download_video(args.url, args.max_height, tmp, args.verbose)
        print(f"Title: {title}")
        try:
            extract_from_video(video, folder, args.interval, args.threshold)
        except RuntimeError as e:
            sys.exit(str(e))
    images = sorted(p.name for p in folder.glob("*.png"))
    print(f"\nSaved {len(images)} photos:")
    for img in images:
        print(f"  {img}")
    print(f"Folder: {folder.resolve()}")
    print(f"Link: {file_link(folder)}")


def extract_from_video(video, folder, interval, threshold, report=cli_report):
    """Save each distinct slide of `video` into `folder`; returns the number saved."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError("Could not open the downloaded video (unsupported codec?).")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total / fps
    step = max(1, int(fps * interval))

    last = None   # fingerprint of last saved slide
    pending = None  # (fingerprint, frame, time) candidate waiting to prove stable
    saved = idx = 0

    def save(frame, t):
        nonlocal saved
        saved += 1
        path = folder / f"slide_{saved:03d}_{fmt_time(t)}.png"
        cv2.imwrite(str(path), frame)

    while True:
        # grab() skips frames without decoding; read the one we want
        for _ in range(step - 1):
            if not cap.grab():
                break
        ok, frame = cap.read()
        if not ok:
            break
        idx += step
        t = idx / fps
        fp = fingerprint(frame)

        if last is None or difference(fp, last) > threshold:
            # New content: only save once it is unchanged for one more sample
            # (avoids saving mid-transition/animation frames).
            if pending is not None and difference(fp, pending[0]) <= threshold / 2:
                save(pending[1], pending[2])
                last, pending = pending[0], None
            else:
                pending = (fp, frame, t)
        else:
            pending = None

        if total and idx < total:
            report("Playing", idx / total, f"{clock(t)}/{clock(duration)}  {saved} screenshots saved")

    # the final candidate never got a confirming sample; keep it
    if pending is not None:
        save(pending[1], pending[2])
    cap.release()
    report("Playing", 1, f"{clock(duration)}/{clock(duration)}  {saved} screenshots saved")
    return saved


def file_link(path):
    return Path(path).resolve().as_uri()


def build_pdf(files, out):
    """Write the image `files` (in the given order) as pages of a PDF at `out`."""
    images = [Image.open(p).convert("RGB") for p in files]
    images[0].save(out, save_all=True, append_images=images[1:], resolution=150)


def generate_pdf(args):
    folder = Path(args.folder)
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"})
    if not files:
        sys.exit(f"No images found in {folder}")
    out = Path(args.output) if args.output else folder / f"{folder.name}.pdf"
    build_pdf(files, out)
    print(f"Wrote {out} ({len(files)} pages)")
    print(f"PDF: {out.resolve()}")
    print(f"Link: {file_link(out)}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("extract-images", help="capture unique slides from a YouTube video")
    c.add_argument("url")
    c.add_argument("--name", required=True, help="session name (used for the output folder name)")
    add_capture_options(c)
    c.set_defaults(func=extract_images)

    d = sub.add_parser("generate-pdf", help="build a PDF from a session folder's images")
    d.add_argument("folder")
    d.add_argument("-o", "--output", help="output PDF path (default: inside the folder)")
    d.set_defaults(func=generate_pdf)

    args = p.parse_args()
    args.func(args)


def add_capture_options(c):
    c.add_argument("--interval", type=float, default=2.0, help="seconds between sampled frames (default 2)")
    c.add_argument("--threshold", type=float, default=0.5,
                   help="percent of pixels that must change to count as a new slide "
                        "(default 0.5; lower = more sensitive)")
    c.add_argument("--verbose", action="store_true", help="show detailed timestamped yt-dlp log lines")
    c.add_argument("--max-height", type=int, default=1080, help="max video resolution (default 1080)")


if __name__ == "__main__":
    main()
