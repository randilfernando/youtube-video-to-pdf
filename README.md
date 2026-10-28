# Youtube Video to PDF

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
Paste a YouTube link, watch the progress, then review the slides: use the ‹ › buttons (or the arrow keys) to move through them and the **Keep** switch (or `x` / space) to drop unwanted ones. **Generate PDF** shows a preview; **Save** stores it in the session folder, named after the video's title. The session is named after the video too, unless you type a name. From there you can download the PDF or upload it to Google Drive.

### Google Drive upload (one-time setup)
Uploads happen in your browser, so the server never stores Google tokens. The OAuth client ID lives in `config.json`; replace it with your own if you fork this.
1. In the [Google Cloud console](https://console.cloud.google.com) create a project and enable the **Google Drive API**.
2. Configure the OAuth consent screen (External) and add your Google account as a test user.
3. Create an OAuth client ID of type **Web application** and add `http://localhost:8000` (the address you open the app at) under **Authorized JavaScript origins**.
4. Put the client ID in `config.json` (`google_client_id`).
5. Click **Upload to Google Drive** and approve the Google popup. The PDF goes into a `SlidesToPDF` folder in your Drive, as `SlidesToPDF/<video title>.pdf`. The app can only see files it uploaded itself.

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
