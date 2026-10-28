# Insta Grabber

Paste an Instagram post / reel link, a Reddit post or a YouTube video, select photos, videos & audio, download them (single file or zip).

## In firehome

firehome runs this as the `down` service in `docker-compose.yml`; nginx proxies
`/down/` to it. Paste a link into the search bar and press **down**: the media
appears in a panel under the bar, each tile a download, plus a zip for carousels.

Cookies go in `down/data/` (mounted read-only at `/data` in the container):
`cookies.txt` for Instagram, `reddit-cookies.txt` for Reddit and
`youtube-cookies.txt` for YouTube. No rebuild or restart is needed after adding
or replacing any of them.

## Reddit

Post links (`/r/<sub>/comments/…`), share links (`/r/<sub>/s/…`), `redd.it/…`
and old/new/mobile reddit all work. Images, galleries and GIFs download as-is.
Reddit videos keep picture and sound in separate streams, so the server merges
them with ffmpeg; expect a few seconds' wait before a video download starts.

Reddit blocks anonymous requests from many IPs (datacenters especially). If the
panel says Reddit wouldn't serve the post, export cookies for reddit.com the same
way as for Instagram below and save them as `down/data/reddit-cookies.txt`.

## YouTube

Video links (`/watch?v=…`, `youtu.be/…`), shorts and ended live streams work;
a video opened from a playlist is taken as that one video. Playlist and channel
links are refused: the panel is one post at a time.

A YouTube video gets a tile per choice rather than per file: the video at up to
1080p, the full size as a second tile when the source is larger, and the audio
on its own as m4a. Every one of them is fetched and merged on the server first
(YouTube's media URLs only work from the IP that asked for them, and picture and
sound are separate streams), so a long video means a long wait before the
download starts — up to a couple of minutes for a 1080p hour.

yt-dlp needs a JavaScript runtime to solve YouTube's player challenges; the
image ships deno for that. Without one, YouTube still works but withholds most
formats. YouTube asks some server IPs to "sign in to confirm you're not a bot";
if the panel says so, export cookies for youtube.com the same way as for
Instagram below and save them as `down/data/youtube-cookies.txt`.

## Run

```bash
cd /root/down
.venv/bin/python app.py                 # http://127.0.0.1:5000
HOST=0.0.0.0 .venv/bin/python app.py    # reachable from other machines on your LAN
```

First-time setup (already done here):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

For YouTube, also have `deno` (or node 22+, enabled with yt-dlp's `js_runtimes`
option) on the PATH; ffmpeg is needed for Reddit and YouTube videos.

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
  The proxy only fetches `*.cdninstagram.com` / `*.fbcdn.net` / `*.redd.it` / `*.ytimg.com` URLs,
  plus YouTube watch URLs, which it hands to yt-dlp rather than fetching itself.
- If extraction breaks after an Instagram change, update yt-dlp: `.venv/bin/pip install -U yt-dlp`.
