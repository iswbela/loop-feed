# loop-feed

A desktop automation tool for managing escort ad descriptions on [Kommons.com](https://www.kommons.com). It uses Playwright to log in, browse your active ads, and update profile descriptions — either by toggling a heart emoji (`❤️`) on each run, or by applying a saved custom message per ad.

The UI is built with Tkinter.

---

## Features

- **Headless browser login** via Playwright (Chromium)
- **Ad picker** with thumbnails — browse your active ads visually
- **Simple mode** — toggles a `❤️` at the end of the current description on each run
- **Custom mode** — apply a saved message to a specific ad
- **Per-ad message management** — store, edit, and reuse multiple messages per ad ID
- **Remember credentials** — optionally persist login info locally (never committed to git)

---

## Project Structure

```
loop-feed/
├── main.py                  # Entry point
├── config.py                # URLs, file paths, constants
├── persistence.py           # Load/save JSON config and messages
├── utils.py                 # Helpers: timestamp, heart toggle logic
├── browser/
│   └── session.py           # PlaywrightSession: login, scrape, update
├── ui/
│   ├── app.py               # Main login window
│   ├── ad_picker.py         # Scrollable ad list with thumbnails
│   ├── edit_window.py       # Simple / custom update mode selector
│   └── messages_window.py   # CRUD UI for per-ad saved messages
├── kommons_messages.json    # Saved messages (gitignored)
└── kommons_config.json      # Saved credentials (gitignored)
```

### Other files

- `kommons.py` — standalone Selenium script (legacy, not used by the main app)
- `debug_token.py` — requests/BeautifulSoup login debugging scaffold

---

## Requirements

- Python 3.9+
- [Playwright](https://playwright.dev/python/) with Chromium installed
- [Pillow](https://python-pillow.org/) for ad thumbnails
- [requests](https://docs.python-requests.org/)

Install dependencies:

```bash
pip install playwright pillow requests
playwright install chromium
```

---

## Usage

```bash
python main.py
```

1. Enter your Kommons email and password, then click **Entrar**
2. Pick an ad from the list
3. Choose **Simple** (heart toggle) or **Custom** (saved message) and confirm

---

## Data Files

| File | Purpose | Committed? |
|------|---------|------------|
| `kommons_config.json` | Saved login credentials | No (gitignored) |
| `kommons_messages.json` | Per-ad custom messages | No (gitignored) |

Debug HTML files (`debug_*.html`) may be written to the project root when the browser can't find an expected element — also gitignored.
