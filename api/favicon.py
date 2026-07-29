#!/usr/bin/env python3
"""Fetch a site's best icon when none was chosen.

Done here rather than in the browser because the page's CSP is
`img-src 'self' data: blob:` — a cross-origin <img> is refused outright, which is
verifiable in any engine. Loosening that to `img-src *` would end the guarantee
that the page makes no external requests, and would leak every viewer's dashboard
contents to each of those hosts. Fetching once, server-side, and serving the
result from our own origin keeps the CSP intact and means the icon still works
when the target is unreachable.

"Best" rather than just /favicon.ico: the page's <link rel> tags are read first,
and an apple-touch-icon (typically 180px) beats a 16px .ico by a wide margin.

    POST /favicon  <- {"url": "https://gitea.lan"}
                   -> {"icon": "fav:<name>", "source": "<url it came from>"}

The stored name is referenced as `fav:<name>`, which the page's iconUrl maps to
/favicons/<name>; that keeps these out of the icon picker, which enumerates the
icon directories.

SSRF: this fetches a URL the user supplied, and cannot refuse private addresses —
pointing at LAN services is the entire purpose. It is bounded instead: http(s)
only, short timeouts, small size caps, and the bytes are stored only if they
actually parse as an image. See the README's risks section.
"""

import hashlib
import os
import re
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

PAGE_BYTES = 256 * 1024
# Tried in order when the page declares no icon of its own. Conventional paths
# that many sites serve without advertising.
WELL_KNOWN = ("/favicon.ico", "/apple-touch-icon.png",
              "/apple-touch-icon-precomposed.png", "/favicon.png")
ICON_BYTES = 512 * 1024
TIMEOUT = 6
USER_AGENT = "firehome-favicon"

# Magic numbers, so only things that really are images get stored.
SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x00\x00\x01\x00", ".ico"),
    (b"RIFF", ".webp"),          # confirmed against WEBP below
)

REL_SCORES = {
    "apple-touch-icon": 700,
    "apple-touch-icon-precomposed": 690,
    "fluid-icon": 600,
    "icon": 400,
    "shortcut icon": 380,
    "mask-icon": 100,
}


def _sniff(data):
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    for magic, ext in SIGNATURES:
        if magic != b"RIFF" and data.startswith(magic):
            return ext
    head = data[:400].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2000]):
        return ".svg"
    return None


class LinkIcons(HTMLParser):
    """Collect <link rel=...icon...> candidates, and stop at </head>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done or tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rel = " ".join(a.get("rel", "").lower().split())
        if "icon" not in rel or not a.get("href"):
            return
        score = REL_SCORES.get(rel, 300 if "icon" in rel else 0)
        # sizes="180x180" — a bigger source beats a nominally better rel.
        largest = 0
        for m in re.finditer(r"(\d+)\s*[xX]\s*(\d+)", a.get("sizes", "")):
            largest = max(largest, int(m.group(1)), int(m.group(2)))
        if a.get("sizes", "").strip().lower() == "any":
            largest = max(largest, 256)
        self.found.append((score + min(largest, 512), a["href"]))

    def handle_endtag(self, tag):
        if tag == "head":
            self.done = True


class FaviconStore:
    def __init__(self, directory, log=print):
        self.dir = directory
        self.log = log

    def writable(self):
        """Whether the cache directory can be written to.

        A named volume mounted where the image has no directory is created
        root-owned, which this unprivileged service cannot write to. Reported as a
        distinct error instead of surfacing as "no icon found".
        """
        try:
            os.makedirs(self.dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".probe-")
            os.close(fd)
            os.unlink(tmp)
            return True
        except OSError:
            return False

    def _get(self, url, limit, truncate=False):
        """Fetch a URL, capped at `limit` bytes.

        truncate=True keeps the first `limit` bytes instead of failing, which is
        what a page wants: <link rel=icon> lives in the <head>, at the very start.
        motogp.com is 541 KB with </head> at byte 7036 — treating the cap as fatal
        threw away a perfectly good 180px apple-touch-icon and fell through to a
        /favicon.ico that 404s. An image is never truncated, because half a file
        is not an image.
        """
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,image/*;q=0.9,*/*;q=0.5",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            # Redirects are followed by urllib, but only ever to http(s).
            if urllib.parse.urlsplit(resp.geturl()).scheme not in ("http", "https"):
                raise ValueError("refused a redirect away from http(s)")
            data = resp.read(limit + 1)
            if len(data) > limit:
                if not truncate:
                    raise ValueError("larger than %d bytes" % limit)
                data = data[:limit]
            return resp.geturl(), data

    def candidates(self, url):
        """Icon URLs for a page, best first, always ending at /favicon.ico."""
        out = []
        try:
            final, page = self._get(url, PAGE_BYTES, truncate=True)
            parser = LinkIcons()
            parser.feed(page.decode("utf-8", "replace"))
            for score, href in sorted(parser.found, key=lambda p: -p[0]):
                joined = urllib.parse.urljoin(final, href)
                if urllib.parse.urlsplit(joined).scheme in ("http", "https"):
                    out.append(joined)
        except Exception as exc:
            self.log("favicon: could not read %s: %r" % (url, exc))
            final = url
        root = urllib.parse.urlsplit(final)
        for path in WELL_KNOWN:
            guess = urllib.parse.urlunsplit((root.scheme, root.netloc, path, "", ""))
            if guess not in out:
                out.append(guess)
        return out

    def fetch(self, url):
        """Resolve, download and store. Returns (name, source) or (None, None)."""
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("only http(s) URLs can be fetched")

        for candidate in self.candidates(url)[:6]:
            try:
                _, data = self._get(candidate, ICON_BYTES)
            except Exception:
                continue
            if not data:
                continue
            ext = _sniff(data)
            if not ext:
                continue
            # Named from the site, not the icon URL, so re-running for the same
            # site replaces its icon rather than accumulating copies.
            stem = hashlib.sha1(
                ("%s://%s" % (parts.scheme, parts.netloc)).encode()
            ).hexdigest()[:16]
            name = stem + ext
            self._write(name, data)
            # Drop any copy stored under a different extension previously.
            for other in os.listdir(self.dir):
                if other.startswith(stem) and other != name:
                    try:
                        os.unlink(os.path.join(self.dir, other))
                    except OSError:
                        pass
            self.log("favicon: %s -> %s (%d bytes)" % (parts.netloc, name, len(data)))
            return name, candidate
        return None, None

    def _write(self, name, data):
        os.makedirs(self.dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".fav-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            # nginx reads these as a different user; see icons.py.
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(self.dir, name))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
