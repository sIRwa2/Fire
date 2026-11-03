# Insta Grabber

Paste an Instagram post / reel link, a Reddit post, a YouTube video or a Vimeo video, select photos, videos & audio, download them (single file or zip).

## In firehome

firehome runs this as the `down` service in `docker-compose.yml`; nginx proxies
`/down/` to it. Paste a link into the search bar and press **down**: the media
appears in a panel under the bar, each tile a download, plus a zip for carousels.

When a site refuses the server (a private or restricted post, a bot check, a
rate limit), the panel says so and shows a paste box for that site's login
cookies; see "Logging in" below. They are kept in `down/data/` (mounted at
`/data` in the container): `cookies.txt` for Instagram, `reddit-cookies.txt`
for Reddit, `youtube-cookies.txt` for YouTube and `vimeo-cookies.txt` for
Vimeo. You can also drop a file in
there by hand. No rebuild or restart is needed after adding or replacing any of
them.

The service runs as uid 10002 and has to be able to write that folder, so
create it once on the docker host before the first paste:

```bash
mkdir -p down/data && chown 10002 down/data
```

If the folder is missing or root-owned, the panel's save fails with exactly that
command in the error.

## Reddit

Post links (`/r/<sub>/comments/…`), share links (`/r/<sub>/s/…`), `redd.it/…`
and old/new/mobile reddit all work. Images, galleries and GIFs download as-is.
Reddit videos keep picture and sound in separate streams, so the server merges
them with ffmpeg; expect a few seconds' wait before a video download starts.

Reddit blocks anonymous requests from many IPs (datacenters especially). If the
panel says Reddit wouldn't serve the post, paste cookies from a reddit.com tab
into the box it shows, the same way as for Instagram below.

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
if the panel says so, paste cookies from a youtube.com tab into the box it
shows, the same way as for Instagram below.

## Vimeo

Video links (`vimeo.com/<id>`, with or without an unlisted hash, and
`player.vimeo.com/video/<id>`) get the same tiles as YouTube: the video at up
to 1080p, the full size when the source is larger, and the audio alone. Every
one is merged on the server first, so expect a wait before the download starts.

Vimeo always needs cookies. Its pages no longer carry the player config, so
yt-dlp reads videos through Vimeo's API, and that API only answers a logged-in
client: the first Vimeo link you paste gets the cookie box straight away. Paste
cookies from a vimeo.com tab where you are logged in, the same way as for
Instagram below. Vimeo also refuses many datacenter IPs with a 403; the panel
shows that as it is, and there is nothing cookies can do about it.

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
option) on the PATH; ffmpeg is needed for Reddit, YouTube and Vimeo videos.

## Logging in (usually needed)

Instagram often refuses anonymous requests, especially from server IPs, and
serves age- or region-restricted posts only to a logged-in account. The page
cannot read Instagram's cookies itself (another origin, and the session cookie
is HttpOnly), so a browser extension exports them and the panel takes the text:

1. Once: in the browser where you're logged in to Instagram, install
   "Get cookies.txt LOCALLY"
   ([Chrome](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc),
   [Firefox](https://addons.mozilla.org/firefox/addon/get-cookies-txt-locally/)).
2. When the panel asks: on an instagram.com tab, open the extension and press
   **Copy**. That puts the Netscape-format export on the clipboard.
3. Paste into the panel's box and press **Save cookies and retry**. The service
   checks the text parses and holds instagram.com cookies, writes
   `down/data/cookies.txt`, and fetches the link again.

The same box appears for Reddit, YouTube and Vimeo and writes their files. Without the
panel, save the exported file by hand as `cookies.txt` next to `app.py` (or
point `IG_COOKIES=/path/to/cookies.txt` at it), or in `down/data/` under docker.

No restart is needed; the file is read on every request. Treat the cookie files
like passwords: anyone who can open the homepage can replace them, and anyone
who can read the folder holds your login. A throwaway account is a good idea,
since heavy scraping can get an account flagged.

## Notes

- Supports `/p/`, `/reel/` and `/tv/` links, including carousels (multiple photos/videos).
- Media is proxied through the server because Instagram's CDN blocks direct cross-site downloads.
  The proxy only fetches `*.cdninstagram.com` / `*.fbcdn.net` / `*.redd.it` / `*.ytimg.com` /
  `*.vimeocdn.com` URLs, plus YouTube and Vimeo page URLs, which it hands to yt-dlp rather than fetching itself.
- If extraction breaks after an Instagram change, update yt-dlp: `.venv/bin/pip install -U yt-dlp`.
