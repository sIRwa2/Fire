"""Vimeo videos, in the shape app.py serves.

Vimeo's pages are a Next.js app now, so yt-dlp reads the video through Vimeo's
API, and that API only answers a logged-in web client: without a vimeo-cookies.txt
every link fails with "only works when logged-in", and the panel asks for the
cookies. Vimeo also blocks many datacenter IPs outright; that comes back as a
403 the panel shows as it is.

Like YouTube, the picture and sound are separate streams (HLS/DASH), so every
item here carries the video's page URL and merge=True, and app.py runs it
through yt-dlp + ffmpeg on download with the item's format key (see FORMATS).
"""
import os
import re
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.extractor.vimeo import VimeoIE

MAX_HEIGHT = 1080
HOSTS = ("vimeo.com", "www.vimeo.com", "player.vimeo.com")


class VimeoError(Exception):
    pass


def suitable(url):
    return VimeoIE.suitable(url)


def is_vimeo(url):
    """Any Vimeo link at all, for a better refusal than 'not a post link'."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host == "vimeo.com" or host.endswith(".vimeo.com")


def _title_name(title, vid, ext):
    name = re.sub(r"[^\w\s-]", "", title or "", flags=re.U).strip()
    name = re.sub(r"\s+", "_", name)[:80].strip("_")
    return f"{name or vid}.{ext}"


def _item(index, kind, fmt, page_url, thumb, title, vid, ext, label):
    return {"index": index, "type": kind, "format": fmt, "url": page_url,
            "thumbnail": thumb, "filename": _title_name(title, vid, ext),
            "kind": label, "merge": True}


def fetch_post(url, cookies=None):
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "cachedir": False}
    if cookies and os.path.isfile(cookies):
        opts["cookiefile"] = cookies
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        raise VimeoError("That's a showcase, channel or album link; paste a link to one video.")
    if info.get("is_live"):
        raise VimeoError("This event is still live; it can be downloaded once it has ended.")
    vid = info["id"]
    title = info.get("title") or ""
    thumb = info.get("thumbnail")
    # The page URL yt-dlp settled on keeps an unlisted video's hash, which the
    # bare id would lose.
    page_url = info.get("webpage_url") or url
    heights = [f.get("height") or 0 for f in info.get("formats") or [] if f.get("vcodec") != "none"]
    best = max(heights, default=0)
    items = [_item(1, "video", "video", page_url, thumb, title, vid, "mp4",
                   f"{min(best, MAX_HEIGHT)}p" if best else "Video")]
    if best > MAX_HEIGHT:
        items.append(_item(2, "video", "video-max", page_url, thumb, title, vid, "mp4", f"{best}p"))
    items.append(_item(len(items) + 1, "audio", "audio", page_url, thumb, title, vid, "m4a", "Audio"))
    uploader = info.get("uploader") or info.get("channel") or ""
    return {
        "shortcode": vid,
        "username": uploader,
        "label": "Vimeo" + (f" · {uploader}" if uploader else ""),
        "caption": title[:300],
        "items": items,
    }
