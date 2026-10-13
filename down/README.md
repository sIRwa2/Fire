# Insta Grabber

Paste an Instagram post / reel link, select photos & videos, download them (single file or zip).

## In firehome

firehome runs this as the `down` service in `docker-compose.yml`; nginx proxies
`/down/` to it. Paste a link into the search bar and press **down**: the media
appears in a panel under the bar, each tile a download, plus a zip for carousels.

Cookies go in `down/data/cookies.txt` (mounted read-only at `/data` in the
container). No rebuild or restart is needed after adding or replacing it.

## Run

```bash
cd /root/down
.venv/bin/python app.py                 # http://127.0.0.1:5000
HOST=0.0.0.0 .venv/bin/python app.py    # reachable from other machines on your LAN
```

First-time setup (already done here):

```bash
python3 -m venv .venv
.venv/bin/pip install flask yt-dlp requests curl_cffi
```

## Logging in (usually needed)

Instagram often refuses anonymous requests, especially from server IPs. To fix that:

1. In a browser where you're logged into Instagram, install a "Get cookies.txt LOCALLY" style extension.
2. Export cookies for instagram.com in Netscape format.
3. Save the file as `cookies.txt` next to `app.py` (or point `IG_COOKIES=/path/to/cookies.txt` at it).

No restart is needed; the file is read on every request. Treat `cookies.txt` like a password.
A throwaway account is a good idea, since heavy scraping can get an account flagged.

## Notes

- Supports `/p/`, `/reel/` and `/tv/` links, including carousels (multiple photos/videos).
- Media is proxied through the server because Instagram's CDN blocks direct cross-site downloads.
  The proxy only fetches `*.cdninstagram.com` / `*.fbcdn.net` URLs.
- If extraction breaks after an Instagram change, update yt-dlp: `.venv/bin/pip install -U yt-dlp`.
