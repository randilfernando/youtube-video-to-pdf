"""Local web UI: paste a YouTube URL, review the slides, build a PDF.

Run with ./serve.sh (or `uvicorn web:app`).
"""
import json
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel

import slides

app = FastAPI()
STATIC = Path(__file__).parent / "static"
SID_RE = re.compile(r"[a-z0-9-]+")
THUMB_WIDTH = 480
MAX_LOG_LINES = 200

JOBS = {}  # session id -> live state of an extraction running in this process


class NewSession(BaseModel):
    url: str
    name: str = ""


class PdfRequest(BaseModel):
    keep: list[str]


def session_dir(sid):
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
    """Human label: saved video title, else the session name from the folder."""
    meta = read_meta(folder)
    if meta.get("title"):
        return meta["title"]
    return re.sub(r"-\d{8}-\d{6}$", "", folder.name).replace("-", " ").strip().title() or folder.name


def created_at(folder):
    m = re.search(r"(\d{8}-\d{6})$", folder.name)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
    return datetime.fromtimestamp(folder.stat().st_mtime)


def pdf_path(folder):
    return folder / f"{folder.name}.pdf"


def preview_path(folder):
    """The PDF as last generated; becomes the saved PDF only when the user confirms."""
    return folder / ".preview.pdf"


def run_job(job, folder, url):
    def report(stage, fraction, detail=""):
        job.update(stage=stage, fraction=fraction, detail=detail)

    def log(msg):
        job["log"] = (job["log"] + [msg])[-MAX_LOG_LINES:]

    try:
        video, title = slides.download_video(url, 1080, folder, verbose=True, report=report, log=log)
        job["title"] = title
        (folder / "session.json").write_text(json.dumps({"title": title, "url": url}))
        slides.extract_from_video(video, folder / slides.SCREENSHOTS, 2.0, 0.5, report)
        job["state"] = "done"
    except Exception as e:  # surfaced to the page
        job.update(state="error", error=str(e))


@app.post("/api/sessions")
def create_session(body: NewSession):
    url = body.url.strip()
    if not re.match(r"https?://", url):
        raise HTTPException(400, "Enter a full YouTube link starting with https://")
    folder = slides.new_session_folder(body.name.strip() or "slides")
    JOBS[folder.name] = {"state": "running", "stage": "Fetching info", "fraction": None,
                         "detail": "", "title": "", "error": "", "log": []}
    threading.Thread(target=run_job, args=(JOBS[folder.name], folder, url), daemon=True).start()
    return {"id": folder.name}


@app.get("/api/sessions")
def list_sessions():
    """Sessions already on disk (newest first), so they can be reopened without re-downloading."""
    out = []
    if slides.SESSIONS_DIR.is_dir():
        for folder in slides.SESSIONS_DIR.iterdir():
            if not folder.is_dir() or not SID_RE.fullmatch(folder.name) or folder.name in JOBS and \
                    JOBS[folder.name]["state"] == "running":
                continue
            names = image_names(folder)
            if names:
                out.append({"id": folder.name, "title": display_name(folder), "slides": len(names),
                            "pdf": pdf_path(folder).exists(), "cover": names[0],
                            "created": created_at(folder).isoformat()})
    return sorted(out, key=lambda s: s["created"], reverse=True)


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    folder = session_dir(sid)
    job = JOBS.get(sid, {"state": "done", "stage": "", "fraction": 1, "detail": "",
                         "title": "", "error": "", "log": []})
    if not job["title"]:
        job = {**job, "title": display_name(folder)}
    return {**job, "id": sid, "images": image_names(folder) if job["state"] != "running" else [],
            "pdf": pdf_path(folder).exists()}


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
    return {"pages": len(files), "bytes": out.stat().st_size}


@app.post("/api/sessions/{sid}/pdf/save")
def save_pdf(sid: str):
    folder = session_dir(sid)
    if not preview_path(folder).exists():
        raise HTTPException(400, "Generate the PDF first")
    out = pdf_path(folder)
    preview_path(folder).replace(out)
    return {"bytes": out.stat().st_size, "path": str(out.resolve())}


@app.get("/api/sessions/{sid}/pdf")
def get_pdf(sid: str, preview: bool = False):
    folder = session_dir(sid)
    out = preview_path(folder) if preview else pdf_path(folder)
    if not out.exists():
        raise HTTPException(404, "PDF not generated yet")
    # inline so the browser can show it in the preview frame
    return FileResponse(out, media_type="application/pdf", filename=pdf_path(folder).name,
                        content_disposition_type="inline")


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str):
    folder = session_dir(sid)
    if JOBS.get(sid, {}).get("state") == "running":
        raise HTTPException(409, "This session is still being processed")
    shutil.rmtree(folder)
    JOBS.pop(sid, None)
    return {"deleted": sid}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
