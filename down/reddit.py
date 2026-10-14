"""Reddit posts: images, galleries, GIFs and videos, in the shape app.py serves.

Reddit refuses anonymous API requests from many server IPs (403, or a bot
challenge page instead of the post). A cookies.txt from a logged-in browser gets
past that, the same way Instagram's does; see README.md.

Videos are the awkward case. v.redd.it stores picture and sound as separate DASH
streams, so the plain video URL has no audio. Those items carry the DASH manifest
and merge=True, and app.py runs them through yt-dlp + ffmpeg on download. The
manifest lives on v.redd.it's public CDN, so the merge itself needs no login.
"""
import html
import os
import re
from http.cookiejar import MozillaCookieJar
from urllib.parse import urlparse

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
HOSTS = ("reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com",
         "np.reddit.com", "m.reddit.com", "redd.it")
POST_ID_RE = re.compile(r"/(?:comments|gallery)/([a-z0-9]+)", re.I)
SHORT_ID_RE = re.compile(r"^/([a-z0-9]+)/?$", re.I)
SHARE_RE = re.compile(r"^/r/[^/]+/s/[A-Za-z0-9]+/?$")
EXT = {"image/jpg": "jpg", "image/jpeg": "jpg", "image/png": "png",
       "image/gif": "gif", "image/webp": "webp"}


class RedditError(Exception):
    pass


def suitable(url):
    try:
        u = urlparse(url)
    except ValueError:
        return False
    return u.scheme in ("http", "https") and (u.hostname or "").lower() in HOSTS


def _session(cookies):
    s = requests.Session()
    s.headers["User-Agent"] = UA
    if cookies and os.path.isfile(cookies):
        jar = MozillaCookieJar(cookies)
        jar.load(ignore_discard=True, ignore_expires=True)
        s.cookies = jar
    return s


def _post_id(s, url):
    u = urlparse(url)
    host = (u.hostname or "").lower()
    m = POST_ID_RE.search(u.path)
    if m:
        return m.group(1)
    if host == "redd.it":
        m = SHORT_ID_RE.match(u.path)
        if m:
            return m.group(1)
    if SHARE_RE.match(u.path) or host == "redd.it":
        # Share links (/r/<sub>/s/<code>) are a redirect to the real post. Reddit
        # answers that redirect even where it blocks the post itself.
        r = s.get(url, allow_redirects=False, timeout=15)
        m = POST_ID_RE.search(urlparse(r.headers.get("Location", "")).path)
        if m:
            return m.group(1)
    raise RedditError("That doesn't look like a link to a Reddit post.")


def _unescape(u):
    return html.unescape(u or "")


def _preview_thumb(post):
    """A ~640px preview, small enough for a tile."""
    images = (post.get("preview") or {}).get("images") or []
    if not images:
        return None
    res = images[0].get("resolutions") or []
    pick = next((r for r in res if r.get("width", 0) >= 480), None) or (res[-1] if res else None)
    return _unescape((pick or images[0].get("source") or {}).get("url")) or None


def _gallery(post, pid):
    meta = post.get("media_metadata") or {}
    items = []
    for i, g in enumerate((post.get("gallery_data") or {}).get("items") or [], 1):
        m = meta.get(g.get("media_id")) or {}
        if m.get("status") != "valid":
            continue
        src = m.get("s") or {}
        previews = m.get("p") or []
        thumb = next((p for p in previews if p.get("x", 0) >= 480), None) or (previews[-1] if previews else None)
        thumb = _unescape((thumb or {}).get("u")) or _unescape(src.get("u")) or None
        if m.get("e") == "AnimatedImage" and (src.get("mp4") or src.get("gif")):
            mp4 = src.get("mp4")
            items.append(_item(i, "video" if mp4 else "image", _unescape(mp4 or src.get("gif")),
                               thumb, src, f"{pid}_{i}.{'mp4' if mp4 else 'gif'}"))
        elif m.get("e") == "Image":
            ext = EXT.get(m.get("m"), "jpg")
            # The i.redd.it original rather than s.u, which is a re-encoded preview.
            items.append(_item(i, "image", f"https://i.redd.it/{g['media_id']}.{ext}",
                               thumb, src, f"{pid}_{i}.{ext}"))
    return items


def _item(index, kind, url, thumb, size, filename, merge=False):
    return {"index": index, "type": kind, "url": url, "thumbnail": thumb,
            "width": size.get("x") or size.get("width"),
            "height": size.get("y") or size.get("height"),
            "filename": filename, "merge": merge}


def _media(post, pid):
    if post.get("is_gallery"):
        return _gallery(post, pid)
    video = ((post.get("secure_media") or post.get("media") or {}).get("reddit_video"))
    if video and video.get("dash_url"):
        return [_item(1, "video", _unescape(video["dash_url"]), _preview_thumb(post),
                      video, f"{pid}.mp4", merge=True)]
    # Imgur gifv and friends: Reddit transcodes them to a silent mp4.
    gif = ((post.get("preview") or {}).get("reddit_video_preview") or {})
    if gif.get("fallback_url"):
        return [_item(1, "video", _unescape(gif["fallback_url"]), _preview_thumb(post),
                      gif, f"{pid}.mp4")]
    url = _unescape(post.get("url_overridden_by_dest") or post.get("url"))
    host = (urlparse(url).hostname or "").lower()
    if host == "i.redd.it" or post.get("post_hint") == "image":
        ext = os.path.splitext(urlparse(url).path)[1].lstrip(".").lower() or "jpg"
        if host.endswith(".redd.it"):
            return [_item(1, "image", url, _preview_thumb(post), {}, f"{pid}.{ext}")]
        # An image hosted elsewhere: Reddit's full-size copy of it.
        src = (((post.get("preview") or {}).get("images") or [{}])[0].get("source") or {})
        if src.get("url"):
            return [_item(1, "image", _unescape(src["url"]), _preview_thumb(post), src, f"{pid}.jpg")]
    return []


def fetch_post(url, cookies=None):
    s = _session(cookies)
    pid = _post_id(s, url)
    r = s.get(f"https://www.reddit.com/comments/{pid}.json", params={"raw_json": 1}, timeout=20)
    if r.status_code in (401, 403, 429) or "json" not in r.headers.get("Content-Type", ""):
        raise RedditError(
            "Reddit wouldn't serve this post to the server (blocked or rate limited). "
            + ("Your reddit-cookies.txt may be expired; re-export it." if cookies and os.path.isfile(cookies)
               else "Add a reddit-cookies.txt from a logged-in browser (see down/README.md)."))
    if r.status_code == 404:
        raise RedditError("That Reddit post doesn't exist, or was removed.")
    r.raise_for_status()
    post = r.json()[0]["data"]["children"][0]["data"]
    # A crosspost carries its media on the original.
    source = (post.get("crosspost_parent_list") or [post])[0]
    items = _media(source, pid)
    if not items:
        raise RedditError("This post has no image or video hosted on Reddit.")
    return {
        "shortcode": pid,
        "username": post.get("author"),
        "label": f"r/{post.get('subreddit')} · u/{post.get('author')}",
        "caption": (post.get("title") or "")[:300],
        "items": items,
    }
