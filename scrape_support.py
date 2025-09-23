# scrape_support.py
import json, re, time, sys
from typing import List, Tuple
import requests
from bs4 import BeautifulSoup as BS

ROOT = "https://wordpress.com/support/"
GUIDES = "https://wordpress.com/support/guides/"
SLEEP = 0.30
TIMEOUT = 30
UA = "WP-NavTool-Scraper/1.2 (+https://github.com/)"

# Clean category URLs. Allow nested subcategories after /category/<parent>/<child>...
CATEGORY_RE = re.compile(r"^https://wordpress\.com/support/category/(?:[a-z0-9-]+/)*[a-z0-9-]+/?$", re.I)

# Clean article URLs under /support/ (exclude non-articles)
ARTICLE_RE = re.compile(
    r"^https://wordpress\.com/support/"
    r"(?!category/|tag/|author/|type/|page/|wp-json|search|embed/|amp/)"
    r"[a-z0-9\-/]+/?$",
    re.I,
)

# Common CTA/global links we never want
TEXT_BLOCKLIST = {
    "how can we help you?",
    "browse our guides",
    "learn from the experts",
    "contact us",
    "contact support",
}

sess = requests.Session()
sess.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})

def log(msg: str): print(msg, file=sys.stderr, flush=True)

def get(url: str) -> requests.Response:
    r = sess.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r

def main_scope(soup: BS) -> BS:
    return soup.select_one("main, #primary, .site-main, .wp-site-blocks") or soup

def title_from_soup(soup: BS) -> str:
    for tag in ("h1","h2","title"):
        el = soup.find(tag)
        if el:
            t = el.get_text(" ", strip=True)
            if t: return t
    return ""

def slug_title(url: str) -> str:
    seg = url.rstrip("/").split("/")[-1]
    return seg.replace("-", " ").strip().title()

def clean_href(a) -> str:
    href = (a.get("href") or "").strip()
    if not href: return ""
    return href.split("#")[0]

# --------------------------
# GUIDES → top-level categories
# --------------------------
def extract_categories() -> List[str]:
    log("[*] Fetching guides…")
    soup = BS(get(GUIDES).text, "html.parser")
    scope = main_scope(soup)
    urls, seen = [], set()
    for a in scope.select('a[href*="/support/category/"]'):
        href = clean_href(a)
        if CATEGORY_RE.match(href) and href not in seen:
            seen.add(href); urls.append(href)
    log(f"[+] Categories: {len(urls)}")
    return urls

# --------------------------
# Category page → (optional) subcategories, then article pages
# --------------------------
def extract_subcategories(cat_url: str) -> List[str]:
    """Some categories are hubs with sub-sections; collect those subcategory URLs."""
    soup = BS(get(cat_url).text, "html.parser")
    scope = main_scope(soup)
    subs, seen = [], set()
    for a in scope.select('a[href*="/support/category/"]'):
        href = clean_href(a)
        txt = a.get_text(" ", strip=True).lower()
        if not href or txt in TEXT_BLOCKLIST: 
            continue
        if CATEGORY_RE.match(href) and href != cat_url and href not in seen:
            seen.add(href); subs.append(href)
    return subs

def extract_articles_from_listing(listing_url: str) -> List[str]:
    """Extract article URLs from a category or subcategory listing page."""
    soup = BS(get(listing_url).text, "html.parser")
    scope = main_scope(soup)

    pages, seen = [], set()

    # Prefer typical article-title anchors
    selectors = [
        ".entry-title a[href]",
        "article .entry-title a[href]",
        ".post .entry-title a[href]",
        ".hentry .entry-title a[href]",
        ".card .entry-title a[href]",
    ]
    found = False
    for sel in selectors:
        for a in scope.select(sel):
            href = clean_href(a)
            if not href: continue
            if ARTICLE_RE.match(href) and href not in seen:
                seen.add(href); pages.append(href); found = True
    # Strict fallback: scan scoped anchors but apply strong filters
    if not found:
        for a in scope.select("a[href]"):
            href = clean_href(a)
            if not href: continue
            txt = a.get_text(" ", strip=True).lower()
            if txt in TEXT_BLOCKLIST: 
                continue
            if ARTICLE_RE.match(href) and href not in seen:
                seen.add(href); pages.append(href)
    return pages

def extract_pages(cat_url: str) -> List[str]:
    """Handle hubs with subcategories; otherwise scrape the category listing itself."""
    # collect subcategories first
    subs = extract_subcategories(cat_url)
    all_pages = []

    if subs:
        log(f"    [*] Subsections: {len(subs)}")
        for s in subs:
            pages = extract_articles_from_listing(s)
            all_pages.extend(pages)
            time.sleep(SLEEP)
    else:
        pages = extract_articles_from_listing(cat_url)
        all_pages.extend(pages)

    # de-dup keep order
    out, seen = [], set()
    for u in all_pages:
        if u not in seen:
            seen.add(u); out.append(u)
    log(f"    [+] Articles found: {len(out)}")
    return out

# --------------------------
# Article page → headings with id
# --------------------------
def extract_anchors(article_url: str) -> Tuple[List[dict], str]:
    try:
        html = get(article_url).text
    except Exception as e:
        log(f"    [!] Fetch error {article_url}: {e}")
        return [], slug_title(article_url)

    soup = BS(html, "html.parser")
    anchors, seen = [], set()
    for h in soup.select("h2[id], h3[id], h4[id]"):
        aid = h.get("id")
        if not aid: continue
        title = h.get_text(" ", strip=True) or f"#{aid}"
        url = f"{article_url}#{aid}"
        if url not in seen:
            seen.add(url); anchors.append({"title": title, "url": url})

    page_title = title_from_soup(soup) or slug_title(article_url)
    return anchors, page_title

# --------------------------
# Build manifest
# --------------------------
def build_manifest() -> dict:
    sections = []
    for cat in extract_categories():
        try:
            log(f"[*] Category: {cat}")
            csoup = BS(get(cat).text, "html.parser")
            section_title = title_from_soup(csoup) or slug_title(cat)

            pages = []
            for p in extract_pages(cat):
                try:
                    anchors, title = extract_anchors(p)
                    pages.append({"title": title, "url": p, "anchors": anchors})
                    time.sleep(SLEEP)
                except Exception as e:
                    log(f"    [!] Skip page {p}: {e}")
                    continue

            sections.append({"section": section_title, "url": cat, "pages": pages})
            time.sleep(SLEEP)
        except Exception as e:
            log(f"[!] Skip category {cat}: {e}")
            continue

    return {
        "version": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": ROOT,
        "sections": sections,
    }

if __name__ == "__main__":
    log("[=] Building manifest…")
    data = build_manifest()
    with open("support-links.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    total_pages = sum(len(s.get("pages", [])) for s in data["sections"])
    log(f"[✓] Wrote support-links.json with {total_pages} pages across {len(data['sections'])} categories.")
