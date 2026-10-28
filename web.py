"""Local web UI: paste a YouTube URL, review the slides, build a PDF.

Run with ./serve.sh (or `uvicorn web:app`).
"""
import json
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel

import slides

app = FastAPI()
STATIC = Path(__file__).parent / "static"
CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())
SID_RE = re.compile(r"[a-z0-9-]+")
THUMB_WIDTH = 480
MAX_LOG_LINES = 200

JOBS = {}     # session id -> live state of an extraction running in this process
ALIASES = {}  # provisional job id -> real session id (the folder is named after the video once its title is known)

class NewSession(BaseModel):
    url: str
    name: str = ""


class RescanRequest(BaseModel):
    threshold: float = 0.5  # percent of pixels that must change to count as a new slide
    interval: float = 2.0   # seconds between sampled frames


class PdfRequest(BaseModel):
    keep: list[str]


def resolve(sid):
    return ALIASES.get(sid, sid)


def session_dir(sid):
    sid = resolve(sid)
    folder = slides.SESSIONS_DIR / sid
    if not SID_RE.fullmatch(sid) or not folder.is_dir():
        raise HTTPException(404, "Unknown session")
    return folder


def image_names(folder):
    return sorted(p.name for p in slides.screenshots_dir(folder).glob("*.png"))


def read_meta(folder):
    try:
        return json.loads((folder / "session.json").read_text())
    except (OSError, ValueError):
        return {}


def display_name(folder):
    """Human label: the name given for the session, else the video title, else the folder name."""
    meta = read_meta(folder)
    if meta.get("name") or meta.get("title"):
        return meta.get("name") or meta["title"]
    return re.sub(r"-\d{8}-\d{6}$", "", folder.name).replace("-", " ").strip().title() or folder.name


def created_at(folder):
    m = re.search(r"(\d{8}-\d{6})$", folder.name)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
    return datetime.fromtimestamp(folder.stat().st_mtime)


def safe_filename(text):
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", text)
    return re.sub(r"\s+", " ", cleaned).strip(" .")[:120] or "slides"


def pdf_name(folder):
    """The PDF is named after the video."""
    return safe_filename(read_meta(folder).get("title") or display_name(folder)) + ".pdf"


def pdf_path(folder):
    return folder / pdf_name(folder)


def find_pdf(folder):
    """The saved PDF, including ones saved before PDFs were named after the video."""
    for p in (pdf_path(folder), folder / f"{folder.name}.pdf"):
        if p.exists():
            return p
    return None


def preview_path(folder):
    """The PDF as last generated; becomes the saved PDF only when the user confirms."""
    return folder / ".preview.pdf"


def run_job(job, jid, url, name):
    def report(stage, fraction, detail=""):
        job.update(stage=stage, fraction=fraction, detail=detail)

    def log(msg):
        job["log"] = (job["log"] + [msg])[-MAX_LOG_LINES:]

    # The video's title (needed to name the folder) is only known after yt-dlp has run,
    # so download into a scratch folder first and move the video into its session folder.
    incoming = slides.SESSIONS_DIR / f".incoming-{jid}"
    try:
        incoming.mkdir(parents=True)
        video, title = slides.download_video(url, 1080, incoming, verbose=True, report=report, log=log)
        folder = slides.new_session_folder(name or title)
        video = Path(shutil.move(str(video), folder / video.name))
        (folder / "session.json").write_text(json.dumps({"title": title, "name": name or title, "url": url}))
        ALIASES[jid] = folder.name
        JOBS[folder.name] = JOBS.pop(jid)
        job["title"] = name or title
        slides.extract_from_video(video, folder / slides.SCREENSHOTS, 2.0, 0.5, report)
        job["state"] = "done"
    except Exception as e:  # surfaced to the page
        job.update(state="error", error=str(e))
    finally:
        shutil.rmtree(incoming, ignore_errors=True)


def run_rescan(job, folder, video, interval, threshold):
    """Re-detect slides from the saved video; the old screenshots are replaced only if this succeeds."""
    def report(stage, fraction, detail=""):
        job.update(stage=stage, fraction=fraction, detail=detail)

    fresh = folder / f"{slides.SCREENSHOTS}.new"
    try:
        shutil.rmtree(fresh, ignore_errors=True)
        slides.extract_from_video(video, fresh, interval, threshold, report)
        shutil.rmtree(folder / slides.SCREENSHOTS, ignore_errors=True)
        shutil.rmtree(folder / ".thumbs", ignore_errors=True)
        preview_path(folder).unlink(missing_ok=True)
        fresh.rename(folder / slides.SCREENSHOTS)
        job["state"] = "done"
    except Exception as e:
        shutil.rmtree(fresh, ignore_errors=True)
        job.update(state="error", error=str(e))


@app.post("/api/sessions")
def create_session(body: NewSession):
    url = body.url.strip()
    if not re.match(r"https?://", url):
        raise HTTPException(400, "Enter a full YouTube link starting with https://")
    jid = f"new-{uuid.uuid4().hex[:8]}"
    JOBS[jid] = {"state": "running", "stage": "Fetching info", "fraction": None,
                 "detail": "", "title": "", "error": "", "log": []}
    threading.Thread(target=run_job, args=(JOBS[jid], jid, url, body.name.strip()), daemon=True).start()
    return {"id": jid}


@app.get("/api/sessions")
def list_sessions():
    """Sessions already on disk (newest first), so they can be reopened without re-downloading."""
    out = []
    if slides.SESSIONS_DIR.is_dir():
        for folder in slides.SESSIONS_DIR.iterdir():
            if not folder.is_dir() or not SID_RE.fullmatch(folder.name) or \
                    JOBS.get(folder.name, {}).get("state") == "running":
                continue
            names = image_names(folder)
            if names:
                out.append({"id": folder.name, "title": display_name(folder), "slides": len(names),
                            "pdf": find_pdf(folder) is not None, "cover": names[0],
                            "created": created_at(folder).isoformat()})
    return sorted(out, key=lambda s: s["created"], reverse=True)


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    sid = resolve(sid)
    job = JOBS.get(sid)
    if job and not (slides.SESSIONS_DIR / sid).is_dir():  # still downloading: no session folder yet
        return {**job, "id": sid, "images": [], "pdf": False, "has_video": False, "pdf_name": ""}
    folder = session_dir(sid)
    job = job or {"state": "done", "stage": "", "fraction": 1, "detail": "", "title": "", "error": "", "log": []}
    if not job["title"]:
        job = {**job, "title": display_name(folder)}
    return {**job, "id": sid, "images": image_names(folder) if job["state"] != "running" else [],
            "pdf": find_pdf(folder) is not None, "pdf_name": pdf_name(folder),
            "has_video": slides.find_video(folder) is not None}


@app.post("/api/sessions/{sid}/rescan")
def rescan(sid: str, body: RescanRequest):
    sid = resolve(sid)
    folder = session_dir(sid)
    video = slides.find_video(folder)
    if not video:
        raise HTTPException(400, "This session has no saved video to re-scan")
    if JOBS.get(sid, {}).get("state") == "running":
        raise HTTPException(409, "This session is already being processed")
    JOBS[sid] = {"state": "running", "stage": "Playing", "fraction": 0, "detail": "Starting…",
                 "title": display_name(folder), "error": "", "log": []}
    threading.Thread(target=run_rescan, daemon=True, args=(
        JOBS[sid], folder, video, min(max(body.interval, 0.5), 10), min(max(body.threshold, 0.05), 20))).start()
    return {"id": sid}


@app.get("/api/sessions/{sid}/images/{name}")
def get_image(sid: str, name: str, thumb: bool = False):
    folder = session_dir(sid)
    if name not in image_names(folder):
        raise HTTPException(404, "Unknown image")
    src = slides.screenshots_dir(folder) / name
    if not thumb:
        return FileResponse(src)
    cached = folder / ".thumbs" / f"{src.stem}.jpg"
    if not cached.exists():
        cached.parent.mkdir(exist_ok=True)
        with Image.open(src) as im:
            im.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 10))
            im.convert("RGB").save(cached, quality=80)
    return FileResponse(cached)


@app.post("/api/sessions/{sid}/pdf")
def make_pdf(sid: str, body: PdfRequest):
    folder = session_dir(sid)
    available = set(image_names(folder))
    files = [slides.screenshots_dir(folder) / n for n in body.keep if n in available]
    if not files:
        raise HTTPException(400, "Keep at least one slide")
    out = preview_path(folder)
    slides.build_pdf(files, out)
    return {"pages": len(files), "bytes": out.stat().st_size, "name": pdf_name(folder)}


@app.post("/api/sessions/{sid}/pdf/save")
def save_pdf(sid: str):
    folder = session_dir(sid)
    if not preview_path(folder).exists():
        raise HTTPException(400, "Generate the PDF first")
    out = pdf_path(folder)
    preview_path(folder).replace(out)
    legacy = folder / f"{folder.name}.pdf"
    if legacy != out:
        legacy.unlink(missing_ok=True)  # replaced by the video-named PDF
    return {"bytes": out.stat().st_size, "path": str(out.resolve()), "name": out.name}


@app.get("/api/sessions/{sid}/pdf")
def get_pdf(sid: str, preview: bool = False):
    folder = session_dir(sid)
    out = preview_path(folder) if preview else find_pdf(folder)
    if not out or not out.exists():
        raise HTTPException(404, "PDF not generated yet")
    # inline so the browser can show it in the preview frame
    return FileResponse(out, media_type="application/pdf", filename=pdf_name(folder),
                        content_disposition_type="inline")


@app.get("/api/config")
def config():
    return {"google_client_id": CONFIG["google_client_id"]}


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str):
    sid = resolve(sid)
    folder = session_dir(sid)
    if JOBS.get(sid, {}).get("state") == "running":
        raise HTTPException(409, "This session is still being processed")
    shutil.rmtree(folder)
    JOBS.pop(sid, None)
    return {"deleted": sid}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
