"""Instagram media downloader: paste a post/reel link, pick media, download."""
import io
import os
import re
import zipfile
from urllib.parse import urlparse

import requests
from flask import Flask, Response, abort, jsonify, request, send_from_directory
from yt_dlp import YoutubeDL
from yt_dlp.extractor.instagram import InstagramIE

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES = os.environ.get("IG_COOKIES", os.path.join(BASE_DIR, "cookies.txt"))
ALLOWED_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

app = Flask(__name__, static_folder=None)


class CapturingInstagramIE(InstagramIE):
    """Reuses yt-dlp's fetching/auth but keeps the raw post data, so photos are included too."""

    def _extract_product(self, product_info, video_id=None, get_comments=True):
        self.captured = product_info
        return {"_type": "playlist", "entries": []}


def _best(candidates):
    candidates = [c for c in candidates or [] if c.get("url")]
    return max(candidates, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0), default=None)


def _media_items(product_info):
    nodes = product_info.get("carousel_media") or [product_info]
    shortcode = product_info.get("code") or "instagram"
    items = []
    for i, node in enumerate(nodes, 1):
        image = _best((node.get("image_versions2") or {}).get("candidates"))
        video = _best(node.get("video_versions"))
        if not image and not video:
            continue
        is_video = video is not None
        items.append({
            "index": i,
            "type": "video" if is_video else "image",
            "url": (video or image)["url"],
            "thumbnail": image["url"] if image else None,
            "width": (video or image).get("width"),
            "height": (video or image).get("height"),
            "filename": f"{shortcode}_{i}.{'mp4' if is_video else 'jpg'}",
        })
    return items


def fetch_post(url):
    opts = {"quiet": True, "no_warnings": True}
    if os.path.isfile(COOKIES):
        opts["cookiefile"] = COOKIES
    with YoutubeDL(opts) as ydl:
        ie = CapturingInstagramIE(ydl)
        ie.captured = None
        ie.extract(url)
        info = ie.captured
    if not info:
        raise RuntimeError("No media found in this post.")
    user = info.get("user") or info.get("owner") or {}
    caption = (info.get("caption") or {}).get("text") or ""
    return {
        "shortcode": info.get("code"),
        "username": user.get("username"),
        "caption": caption[:300],
        "items": _media_items(info),
    }


def _check_cdn_url(url):
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or not host.endswith(ALLOWED_HOST_SUFFIXES):
        abort(400, "Only Instagram CDN URLs are allowed.")


def _safe_name(name, default):
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name or "")[:100]
    return name or default


@app.get("/")
def index():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "index.html")


@app.post("/api/info")
def info():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not InstagramIE.suitable(url):
        return jsonify(error="That doesn't look like an Instagram post, reel or TV link."), 400
    try:
        return jsonify(fetch_post(url))
    except Exception as e:  # yt-dlp raises many error types; surface the message
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
        msg = re.sub(r"^\[\w+\] \S+: ", "", msg)
        if re.search(r"cookies|login|rate-limit|empty media response", msg, re.I):
            msg = ("Instagram wouldn't serve this post anonymously (private post or rate limit). "
                   + ("Your cookies.txt may be expired; re-export it." if os.path.isfile(COOKIES)
                      else "Add a cookies.txt from a logged-in browser (see down/README.md)."))
        return jsonify(error=msg), 502


@app.get("/api/file")
def file():
    url = request.args.get("url", "")
    _check_cdn_url(url)
    name = _safe_name(request.args.get("name"), "media")
    upstream = requests.get(url, stream=True, timeout=30, headers={"User-Agent": UA})
    if upstream.status_code != 200:
        abort(502, f"Instagram CDN returned {upstream.status_code}")
    preview = request.args.get("inline") == "1"
    headers = {
        "Content-Type": upstream.headers.get("Content-Type", "application/octet-stream"),
        "Content-Disposition": f'{"inline" if preview else "attachment"}; filename="{name}"',
    }
    if "Content-Length" in upstream.headers:
        headers["Content-Length"] = upstream.headers["Content-Length"]
    return Response(upstream.iter_content(64 * 1024), headers=headers)


@app.post("/api/zip")
def zip_files():
    data = request.get_json(silent=True) or {}
    files = data.get("files") or []
    if not files:
        abort(400, "No files selected.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for i, f in enumerate(files, 1):
            _check_cdn_url(f.get("url", ""))
            r = requests.get(f["url"], timeout=60, headers={"User-Agent": UA})
            r.raise_for_status()
            zf.writestr(_safe_name(f.get("filename"), f"media_{i}"), r.content)
    zip_name = _safe_name(data.get("name"), "instagram") + ".zip"
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{zip_name}"'})


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)))
