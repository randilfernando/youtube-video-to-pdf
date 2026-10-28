# youtube-video-to-pdf

Capture the distinct slides from a YouTube video, review them, then build a PDF.

## Setup
```
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Interactive use
```
./run.sh
```
Asks for the URL and session name, shows capture progress, lists the saved photos with a folder link, waits for you to review/delete images, then on `y` builds the PDF and prints its link. Extra flags (e.g. `--threshold 2`) are passed to the extract-images step.

## Non-interactive use (scripts / AI agents)
`slides.py` never prompts.
```
python slides.py extract-images "<url>" --name "<name>" [--interval 2] [--threshold 3] [--max-height 1080]
python slides.py generate-pdf sessions/<folder> [-o out.pdf]
```
`extract-images` creates `sessions/<name>-<timestamp>/` and prints `Folder: <path>` and `Link: file://...` on its last lines; `generate-pdf` prints `PDF: <path>` and `Link: file://...`. Delete unwanted images between the two steps (files are ordered by name).
