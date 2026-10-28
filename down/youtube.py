"""YouTube videos, shorts and live replays, in the shape app.py serves.

YouTube serves picture and sound as separate streams above 360p, and its media
URLs are tied to the IP that fetched them and expire within hours. So there is
nothing the panel can link to directly: every item here carries the watch URL
and merge=True, and app.py runs it through yt-dlp + ffmpeg on download, picking
the streams with the item's format key (see FORMATS there).

yt-dlp needs a JavaScript runtime (deno, in the image) to solve YouTube's player
challenges; without one it still works, but YouTube withholds most formats and
throttles the rest. A youtube-cookies.txt is only needed when YouTube asks the
server to "sign in to confirm you're not a bot", which it does to some
datacenter IPs; see README.md.
"""
import os
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from yt_dlp import YoutubeDL
from yt_dlp.extractor.youtube import YoutubeIE

MAX_HEIGHT = 1080


class YouTubeError(Exception):
    pass


def _video_url(url):
    """The link with playlist parameters dropped, so a video opened from a playlist
    is taken as that one video rather than refused as a playlist."""
    try:
        u = urlparse(url)
    except ValueError:
        return url
    if u.scheme not in ("http", "https"):
        return url
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
         if k not in ("list", "index", "start_radio", "pp", "si", "feature", "t")]
    return urlunparse(u._replace(query=urlencode(q)))


def suitable(url):
    return YoutubeIE.suitable(_video_url(url))


def is_youtube(url):
    """Any YouTube link at all, for a better refusal than 'not a post link'."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("youtube.com", "youtu.be") or host.endswith(".youtube.com")


def _title_name(title, vid, ext):
    name = re.sub(r"[^\w\s-]", "", title or "", flags=re.U).strip()
    name = re.sub(r"\s+", "_", name)[:80].strip("_")
    return f"{name or vid}.{ext}"


def _item(index, kind, fmt, vid, thumb, title, ext, label):
    return {"index": index, "type": kind, "format": fmt,
            "url": f"https://www.youtube.com/watch?v={vid}", "thumbnail": thumb,
            "filename": _title_name(title, vid, ext), "kind": label, "merge": True}


def fetch_post(url, cookies=None):
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "cachedir": False}
    if cookies and os.path.isfile(cookies):
        opts["cookiefile"] = cookies
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(_video_url(url), download=False)
    if info.get("_type") == "playlist":
        raise YouTubeError("That's a playlist or channel link; paste a link to one video.")
    if info.get("is_live"):
        raise YouTubeError("This stream is still live; it can be downloaded once it has ended.")
    vid = info["id"]
    title = info.get("title") or ""
    thumb = info.get("thumbnail")
    heights = [f.get("height") or 0 for f in info.get("formats") or [] if f.get("vcodec") != "none"]
    best = max(heights, default=0)
    items = [_item(1, "video", "video", vid, thumb, title, "mp4",
                   f"{min(best, MAX_HEIGHT)}p" if best else "Video")]
    if best > MAX_HEIGHT:
        items.append(_item(2, "video", "video-max", vid, thumb, title, "mp4", f"{best}p"))
    items.append(_item(len(items) + 1, "audio", "audio", vid, thumb, title, "m4a", "Audio"))
    channel = info.get("channel") or info.get("uploader") or ""
    return {
        "shortcode": vid,
        "username": channel,
        "label": "YouTube" + (f" · {channel}" if channel else ""),
        "caption": title[:300],
        "items": items,
    }
