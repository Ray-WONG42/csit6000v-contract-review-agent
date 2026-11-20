"""Bounded public-web retrieval with DNS pinning and redirect validation."""
import http.client
import ipaddress
import json
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin, quote
import xml.etree.ElementTree as ET
import urllib.request

LIMIT = 1_500_000


def public_address(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    # Some local VPNs return synthetic DNS. Resolve via fixed HTTPS DNS and
    # pin the verified public IP, instead of allowing arbitrary private targets.
    if ips and all(ip in ipaddress.ip_network("198.18.0.0/15") for ip in ips):
        request = urllib.request.Request("https://cloudflare-dns.com/dns-query?name=" + quote(host) + "&type=A",
                                         headers={"accept": "application/dns-json"})
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = response.read(50000)
        data = json.loads(payload)
        ips = [ipaddress.ip_address(item["data"]) for item in data.get("Answer", []) if item.get("type") == 1]
    if not ips or any(not ip.is_global for ip in ips):
        raise ValueError("仅允许读取公开互联网地址")
    return str(ips[0])


class PublicHTTPS(http.client.HTTPSConnection):
    def connect(self):
        address = public_address(self.host)
        # Connect to the validated address. TLS still checks the original hostname.
        self.sock = socket.create_connection((address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def fetch_public(url):
    for _ in range(5):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("网页工具支持标准 HTTPS 公共地址")
        host = parsed.hostname.encode("idna").decode()
        connection = PublicHTTPS(host, timeout=12, context=ssl.create_default_context())
        try:
            connection.request("GET", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                               headers={"User-Agent": "ClauseResearch/1.0", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader("Location", ""))
                continue
            if response.status != 200:
                raise ValueError(f"网页返回 HTTP {response.status}")
            media = response.getheader("Content-Type", "").lower()
            if not any(kind in media for kind in ("text/", "xml", "json")):
                raise ValueError("此链接不是可读取的文字网页")
            raw = response.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError("网页超过读取大小限制")
            return url, raw.decode("utf-8", errors="replace")
        finally:
            connection.close()
    raise ValueError("网页重定向次数过多")


class TextPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.blocks.append(data.strip())


def read_webpage(url):
    final, html = fetch_public(url)
    page = TextPage()
    page.feed(html)
    return {"url": final, "text": "\n".join(page.blocks)[:14000], "retrieved_at": time.time()}


def web_search(query):
    from .config import settings
    if settings.brave_search_key:
        import urllib.request
        request = urllib.request.Request("https://api.search.brave.com/res/v1/web/search?q=" + quote(query) + "&count=5",
                                         headers={"X-Subscription-Token": settings.brave_search_key, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
        items = data.get("web", {}).get("results", [])
        results = [{"title": item["title"], "url": item["url"], "snippet": item.get("description", "")[:800]} for item in items[:5]]
        provider = "Brave Search"
    else:
        _, xml = fetch_public("https://www.bing.com/search?format=rss&q=" + quote(query))
        root = ET.fromstring(xml)
        results = [{"title": item.findtext("title") or "", "url": item.findtext("link") or "",
                    "snippet": (item.findtext("description") or "")[:800]} for item in root.findall("./channel/item")[:5]]
        provider = "Bing RSS"
    results = [r for r in results if r["url"].startswith("https://")]
    if not results:
        raise ValueError("搜索服务未返回结果，请稍后重试或配置 Brave Search")
    return {"provider": provider, "query": query, "results": results, "retrieved_at": time.time()}
