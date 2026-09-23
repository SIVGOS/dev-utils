# dev-utils

Small local, no-account, browser-based dev tools, served from a tiny local
Python API server. Everything runs on `localhost` only.

## Tools

- **Markdown Viewer** (`md-viewer.html`) — open any `.md` file from disk in
  the browser (`?file=/path/to/doc.md`), browse directories, renders Mermaid
  diagrams inline, and exports the doc to Word (`.docx`) — including the
  Mermaid diagrams as embedded images.
- **DBeaver Text → Markdown Converter** (`dbeaver-md-table.html`) — paste
  DBeaver query output and get a Markdown table back.
- `index.html` — landing page linking the tools above.

## Prerequisites

- Python 3.10+
- the `pandoc` CLI binary — `sudo apt install pandoc` (or `brew install pandoc` on macOS)
- Node.js + npm — only needed for rendering Mermaid diagrams in the Word export
- a system Chrome or Chromium browser — used headless by the Mermaid renderer

## Setup

```bash
git clone git@github.com:SIVGOS/dev-utils.git ~/Desktop/mytools
cd ~/Desktop/mytools

# Python venv — server.py runs from this, isolated from your system Python
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Node deps — mermaid-filter, used by the Word export to render Mermaid
# diagrams. Skips downloading its own ~300MB Chromium; server.py points it
# at your system Chrome/Chromium at runtime instead.
PUPPETEER_SKIP_DOWNLOAD=true PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true npm install
```

If you skip the Node step, everything still works — Mermaid fences in the
Word export just fall back to plain code blocks instead of images.

### Shell integration (the `myserver` command)

Add this to `~/.bashrc` (or `~/.zshrc`):

```bash
myserver() { "$HOME/Desktop/mytools/serve.sh" "$@"; }
```

Reload your shell (`source ~/.bashrc`), then:

```bash
myserver            # start (if needed) and open the index page
myserver stop
myserver restart
myserver status
myserver log         # tail the request log
```

- Override the port: `MYSERVER_PORT=9000 myserver`
- Override which directory the file-read API may reach (default `$HOME`):
  `MYSERVER_ROOT=/some/dir myserver`

If `~/Desktop/mytools` isn't where you cloned this repo, adjust the path in
the `myserver` function above accordingly.

## How it fits together

`server.py` is a small stdlib-only HTTP server that does two things:

1. Serves the static `.html` tools in this directory.
2. Backs them with a minimal read-only file API (`/api/list`, `/api/read`)
   and a Markdown → `.docx` export endpoint (`/api/export-docx`), since a
   browser page on its own can't read arbitrary files from disk.

It binds to `127.0.0.1` only and rejects any request whose `Origin`/`Host`
isn't `localhost`, so it's meant for personal local use, not for exposing on
a network.

## Repo layout

```
server.py               local API server (file list/read, docx export)
serve.sh                start/stop/restart/status wrapper — the `myserver` command
md-viewer.html           Markdown viewer + Word export UI
dbeaver-md-table.html    DBeaver output → Markdown table converter
index.html               landing page
requirements.txt        Python deps (currently none — kept for future use)
package.json             Node deps (mermaid-filter, for diagram rendering)
```
