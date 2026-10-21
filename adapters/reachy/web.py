"""Read-only web lookups: search through a SearXNG instance, and fetch one public page as plain text.

Nothing here can post, log in or download files. Page fetches only reach public internet addresses, so
a link in a search result can't be used to reach the LAN (the robot, other agents, the console).
"""

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

MAX_BYTES = 1_000_000
UA = "Mozilla/5.0 (compatible; majel-reachy; read-only lookup)"


def search(base_url, query, count=5, timeout=8):
    """Top results as [{title, url, snippet}], plus any instant answers SearXNG found."""
    r = requests.get(f"{base_url.rstrip('/')}/search", params={"q": query, "format": "json", "safesearch": 1},
                     timeout=timeout)
    r.raise_for_status()
    data = r.json()
    answers = [a.get("answer", "") if isinstance(a, dict) else str(a) for a in data.get("answers", [])]
    results = [{"title": x.get("title", "")[:120], "url": x.get("url", ""), "snippet": x.get("content", "")[:300]}
               for x in data.get("results", [])[:count]]
    return [a for a in answers if a][:2], results


def is_public(host):
    """True only if every address the host resolves to is a public internet address."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    addrs = {ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos}
    return bool(addrs) and all(a.is_global for a in addrs)


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "template"}

    def __init__(self):
        super().__init__()
        self.parts, self.depth, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        self._in_title = tag == "title"

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        if tag in ("p", "div", "li", "br", "h1", "h2", "h3", "tr"):
            self.parts.append("\n")
        self._in_title = False

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
        elif not self.depth:
            self.parts.append(data)


def fetch_text(url, limit=2500, timeout=8, max_redirects=3):
    """Fetch a public http(s) page and return (title, text), text cut to `limit` characters.

    Raises ValueError for anything that isn't a public web page. Each redirect hop is checked again.
    (A host that changes its DNS between the check and the request could still slip through; the
    guard is meant to stop links to LAN names and addresses, not a determined DNS attacker.)
    """
    for _ in range(max_redirects + 1):
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("only http and https pages can be read")
        if parts.username or parts.password:
            raise ValueError("links with credentials are refused")
        if not is_public(parts.hostname):
            raise ValueError("that address isn't on the public internet")
        r = requests.get(url, timeout=timeout, stream=True, allow_redirects=False, headers={"User-Agent": UA})
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers.get("Location", ""))
            r.close()
            continue
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if not ctype.startswith(("text/html", "text/plain", "application/xhtml")):
            r.close()
            raise ValueError(f"not a readable page ({ctype.split(';')[0] or 'unknown type'})")
        body = b""
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) >= MAX_BYTES:
                break
        r.close()
        text = body.decode(r.encoding or "utf-8", errors="replace")
        if ctype.startswith("text/plain"):
            return "", re.sub(r"\s+", " ", text).strip()[:limit]
        parser = _Text()
        parser.feed(text)
        clean = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.parts))
        clean = re.sub(r"\s*\n\s*", "\n", clean).strip()
        return parser.title[:120], clean[:limit]
    raise ValueError("too many redirects")
