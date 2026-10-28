# youtube-video-to-pdf

Capture the distinct slides from a YouTube video, review them, then build a PDF.

## Setup
```
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Web UI
```
./serve.sh        # then open http://localhost:8000
```
Paste a YouTube link, watch the progress, then review the slides: use the ‹ › buttons (or the arrow keys) to move through them and the **Keep** switch (or `x` / space) to drop unwanted ones. **Generate PDF** saves the PDF in the session folder and lets you download it or share it on WhatsApp (on devices whose browser can share files; otherwise it downloads the PDF and opens WhatsApp so you can attach it).

To open it from a phone or another computer on the same network, run `HOST=0.0.0.0 ./serve.sh`. There is no login, so only do this on a network you trust.

## Interactive use (terminal)
```
./run.sh
```
Asks for the URL and session name, shows capture progress, lists the saved photos with a folder link, waits for you to review/delete images, then on `y` builds the PDF and prints its link. Extra flags (e.g. `--threshold 0.2`) are passed to the extract-images step.

## Non-interactive use (scripts / AI agents)
`slides.py` never prompts.
```
python slides.py extract-images "<url>" --name "<name>" [--interval 2] [--threshold 0.5] [--max-height 1080]
python slides.py generate-pdf sessions/<folder> [-o out.pdf]
```
`extract-images` creates `sessions/<name>-<timestamp>/` and prints `Folder: <path>` and `Link: file://...` on its last lines; `generate-pdf` prints `PDF: <path>` and `Link: file://...`. Delete unwanted images between the two steps (files are ordered by name).
