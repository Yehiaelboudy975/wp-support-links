# scrape_support.py
# Build a clean manifest of Categories → Pages → Anchors from
# https://wordpress.com/support/guides/ and friends.
#
# Output: support-links.json at repo root.

import json
import re
import time
import sys
from typing import List, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup as BS

# ----------------------------
# Config
# ----------------------------
ROOT = "https://wordpress.com/support/"
GUIDES = "https://wordpress.com/support/guides/"

SLEEP = 0.35                   # be polite between HTTP requests
TIMEOUT = 30                   # seconds
UA = "WP-NavTool-Scraper/1.1 (+https://github.com/)"

# Accept ONLY clean category URLs (no fragments/query, single path after /category/)
CATEGORY_RE = re.compile(r"^https://wordpress\.com/support/category/[^/?#]+/?$")

# Accept ONLY clean article URLs under /support/, excluding these sections
ARTICLE_RE = re.compile(
    r"^https://wordpress\.com/support/"
    r"(?!category/|tag/|author/|type/|page/|wp-json|search|embed/|amp/)"
    r"[^?#]+/?$"
)

# Optional: pin to known 12 categories (uncomment to enforce strict allowlist)
# ALLOWED_CATEGORIES = {
#   "get-started", "design", "writing-and-editing", "media", "pages",
#   "themes", "plugins", "marketplace", "domains", "privacy-and-security",
#   "payments", "advanced"
# }

# ----------------------------
# HTTP helpers
# ----------------------------
sess = requests.Session()
sess.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})

def get(url: str) -> requests.Response:
    r = sess.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r

def log(msg: str):
    print(msg, file=sys.stderr, flush=True)

# ----------------------------
# Extractors
# ----------------------------
def extract_title_from_soup(soup: BS) -> str:
    for tag in ("h1", "h2", "title"):
        el = soup.find(tag)
        if el:
            txt = el.get_text(" ", strip=True)
            if txt:
                return txt
    return ""

def slug_to_title(url: str) -> str:
    seg = url.rstrip("/").split("/")[-1]
    return seg.replace("-", " ").strip().title()

def extract_categories() -> List[str]:
    """Return clean category URLs from /support/guides/."""
    log("[*] Fetch guides page…")
    soup = BS(get(GUIDES).text, "html.parser")

    # Prefer main content region if present
    scope = soup.select_one("main, #primary, .site-main") or soup

    links = []
    for a in scope.select('a[href*="/support/category/"]'):
        href = (a.get("href") or "").split("#")[0]
        if CATEGORY_RE.match(href):
            # # Optional allowlist
            # if 'ALLOWED_CATEGORIES' in globals():
            #     slug = href.rstrip("/").split("/")[-1]
            #     if slug not in ALLOWED_CATEGORIES:
            #         continue
            links.append(href)

    # de-dup keep order
    out, seen = [], set()
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)

    log(f"[+] Found {len(out)} categories")
    return out

def extract_pages(category_url: str) -> List[str]:
    """Return clean article URLs for a category, with pagination support."""
    pages = []
    seen_pages = set()
    seen_paginate = set()
    next_url = category_url

    while next_url and next_url not in seen_paginate:
        seen_paginate.add(next_url)
        log(f"    [*] Category page: {next_url}")
        soup = BS(get(next_url).text, "html.parser")

        # 1) Prefer article title anchors inside common layouts
        selectors = [
            ".entry-title a[href]",
            "article .entry-title a[href]",
            ".card a[href][rel!=category]",   # generic card link fallback
        ]
        found_any = False
        for sel in selectors:
            for a in soup.select(sel):
                href = (a.get("href") or "").split("#")[0]
                if ARTICLE_RE.match(href) and href not in seen_pages:
                    pages.append(href)
                    seen_pages.add(href)
                    found_any = True
        # 2) Fallback: scan all anchors strictly
        if not found_any:
            for a in soup.select("a[href]"):
                href = (a.get("href") or "").split("#")[0]
                if ARTICLE_RE.match(href) and href not in seen_pages:
                    pages.append(href)
                    seen_pages.add(href)

        # Pagination "next"
        nxt = (
            soup.select_one('a[rel="next"]') or
            soup.select_one("a.next") or
            soup.select_one(".pagination a.next, .nav-links a.next")
        )
        next_url = nxt.get("href") if nxt and nxt.has_attr("href") else None

        time.sleep(SLEEP)

    log(f"    [+] Articles found: {len(pages)}")
    return pages

def extract_anchors(article_url: str) -> Tuple[List[dict], str]:
    """Return anchors (headings with id) and page title for an article."""
    try:
        html = get(article_url).text
    except Exception as e:
        log(f"    [!] Error fetching article: {article_url} ({e})")
        return [], slug_to_title(article_url)

    soup = BS(html, "html.parser")
    anchors = []

    # Only headings with id → clean & stable
    for h in soup.select("h2[id], h3[id], h4[id]"):
        aid = h.get("id")
        if not aid:
            continue
        title = h.get_text(" ", strip=True) or f"#{aid}"
        anchors.append({"title": title, "url": f"{article_url}#{aid}"})

    # de-dup by URL
    out, seen = [], set()
    for a in anchors:
        if a["url"] not in seen:
            seen.add(a["url"])
            out.append(a)

    page_title = extract_title_from_soup(soup) or slug_to_title(article_url)
    return out, page_title

# ----------------------------
# Build manifest
# ----------------------------
def build_manifest() -> dict:
    sections = []
    for cat_url in extract_categories():
        try:
            log(f"[*] Category: {cat_url}")
            cat_soup = BS(get(cat_url).text, "html.parser")
            section_title = extract_title_from_soup(cat_soup) or slug_to_title(cat_url)

            page_urls = extract_pages(cat_url)
            pages = []

            for p in page_urls:
                try:
                    anchors, title = extract_anchors(p)
                    pages.append({"title": title, "url": p, "anchors": anchors})
                    time.sleep(SLEEP)
                except Exception as e:
                    log(f"    [!] Skipping page (parse error): {p} ({e})")
                    continue

            sections.append({"section": section_title, "url": cat_url, "pages": pages})
            time.sleep(SLEEP)

        except Exception as e:
            log(f"[!] Skipping category (error): {cat_url} ({e})")
            continue

    manifest = {
        "version": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": ROOT,
        "sections": sections,
    }
    return manifest

# ----------------------------
# Entrypoint
# ----------------------------
if __name__ == "__main__":
    log("[=] Building manifest…")
    data = build_manifest()
    with open("support-links.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"[✓] Wrote support-links.json with {sum(len(s.get('pages',[])) for s in data['sections'])} pages across {len(data['sections'])} categories.")
