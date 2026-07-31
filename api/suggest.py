"""Search suggestions, proxied.

The dashboard cannot fetch an engine's suggestion endpoint directly: none of
them send Access-Control-Allow-Origin, so the browser blocks the response, and
the page's own CSP pins connect-src to 'self' anyway. This fetches them
server-side and hands back a plain JSON array of strings.

Unlike favicon.py this does NOT fetch a user-supplied URL. The upstream is
picked from ENGINES by key, so the query string is the only thing the caller
influences and there is no SSRF surface to bound — an unknown key is a 400,
not a fetch. Self-hosted engines are deliberately absent: a SearXNG instance
needs its own JSON API enabled and returns a different shape, so guessing at
one would send the user's keystrokes to a host that may not expect them.

Suggestions mean every keystroke reaches the engine, which is a real privacy
cost and the reason this is opt-in rather than on by default.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 4
MAX_BYTES = 64 * 1024
MAX_QUERY = 200
MAX_SUGGESTIONS = 8
USER_AGENT = "firehome-suggest"

# key -> URL template. %s is the percent-encoded query.
#
# Both endpoints answer with the OpenSearch array form,
# ["typed", ["first", "second", ...]], which is why one parser covers both.
ENGINES = {
    "duckduckgo": "https://duckduckgo.com/ac/?q=%s&type=list",
    "google": "https://suggestqueries.google.com/complete/search?client=firefox&q=%s",
    "bing": "https://api.bing.com/osjson.aspx?query=%s",
    "startpage": "https://suggestqueries.google.com/complete/search?client=firefox&q=%s",
}


def _parse(raw):
    """Pull the suggestion list out of an OpenSearch-style response.

    Shape is ["query", ["a", "b", ...], ...]. Anything else yields nothing
    rather than raising: a changed upstream format should degrade to "no
    suggestions", not break the dashboard's search box.
    """
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(doc, list) or len(doc) < 2:
        return []
    items = doc[1]
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        # Some engines return [text, type] pairs rather than bare strings.
        if isinstance(item, list) and item:
            item = item[0]
        if isinstance(item, str):
            text = item.strip()
            if text and text not in out:
                out.append(text)
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


def fetch(engine, query):
    """Suggestions for `query` from `engine`.

    Returns (list, error). A network failure or an unparseable response is an
    empty list, never an exception: the search box has to keep working when the
    engine is unreachable, which on a LAN-only box is the normal case.
    """
    if engine not in ENGINES:
        return [], "unknown engine"
    query = (query or "").strip()
    if not query:
        return [], None
    if len(query) > MAX_QUERY:
        query = query[:MAX_QUERY]
    url = ENGINES[engine] % urllib.parse.quote(query, safe="")
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/javascript, */*",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(MAX_BYTES)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return [], "upstream unreachable: %s" % exc
    # Charset varies by engine (Google answers in ISO-8859-1), and a decode
    # error should not surface as a 500.
    for encoding in ("utf-8", "latin-1"):
        try:
            return _parse(raw.decode(encoding)), None
        except UnicodeDecodeError:
            continue
    return [], "undecodable response"
