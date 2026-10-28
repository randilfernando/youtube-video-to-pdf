"""Local web UI: paste a YouTube URL, review the slides, build a PDF.

Run with ./serve.sh (or `uvicorn web:app`).
"""
import re
import tempfile
import threading
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
    return sorted(p.name for p in folder.glob("*.png"))


def pdf_path(folder):
    return folder / f"{folder.name}.pdf"


def run_job(job, folder, url):
    def report(stage, fraction, detail=""):
        job.update(stage=stage, fraction=fraction, detail=detail)

    def log(msg):
        job["log"] = (job["log"] + [msg])[-MAX_LOG_LINES:]

    try:
        with tempfile.TemporaryDirectory() as tmp:
            video, title = slides.download_video(url, 1080, tmp, verbose=True, report=report, log=log)
            job["title"] = title
            slides.extract_from_video(video, folder, 2.0, 0.5, report)
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


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    folder = session_dir(sid)
    job = JOBS.get(sid, {"state": "done", "stage": "", "fraction": 1, "detail": "",
                         "title": "", "error": "", "log": []})
    return {**job, "id": sid, "images": image_names(folder) if job["state"] != "running" else [],
            "pdf": pdf_path(folder).exists()}


@app.get("/api/sessions/{sid}/images/{name}")
def get_image(sid: str, name: str, thumb: bool = False):
    folder = session_dir(sid)
    if name not in image_names(folder):
        raise HTTPException(404, "Unknown image")
    src = folder / name
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
    files = [folder / n for n in body.keep if n in available]
    if not files:
        raise HTTPException(400, "Keep at least one slide")
    out = pdf_path(folder)
    slides.build_pdf(files, out)
    return {"pages": len(files), "bytes": out.stat().st_size, "path": str(out.resolve())}


@app.get("/api/sessions/{sid}/pdf")
def download_pdf(sid: str):
    out = pdf_path(session_dir(sid))
    if not out.exists():
        raise HTTPException(404, "PDF not generated yet")
    return FileResponse(out, media_type="application/pdf", filename=out.name)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
