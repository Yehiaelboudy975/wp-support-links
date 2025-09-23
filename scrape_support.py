import json, time
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup as BS

ROOT = "https://wordpress.com/support/"
GUIDES = "https://wordpress.com/support/guides/"

SLEEP = 0.35  # be polite
UA = "WP-NavTool-Scraper/1.0 (+https://github.com/)"

sess = requests.Session()
sess.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})

def get(url):
  r = sess.get(url, timeout=30)
  r.raise_for_status()
  return r

def is_support_article(href: str) -> bool:
  # Article URLs live under /support/ but are not /category/
  return href.startswith("https://wordpress.com/support/") and "/category/" not in href

def extract_categories():
  soup = BS(get(GUIDES).text, "html.parser")
  links = []
  for a in soup.select('a[href*="/support/category/"]'):
    href = a.get("href", "").split("#")[0]
    if href:
      links.append(href)
  # de-dup preserve order
  out, seen = [], set()
  for u in links:
    if u not in seen:
      seen.add(u); out.append(u)
  return out

def extract_title_from_soup(soup: BS):
  for tag in ["h1", "h2", "title"]:
    el = soup.find(tag)
    if el:
      txt = el.get_text(" ", strip=True)
      if txt: return txt
  return None

def extract_pages(category_url: str):
  pages = []
  next_url = category_url
  seen = set()
  while next_url and next_url not in seen:
    seen.add(next_url)
    soup = BS(get(next_url).text, "html.parser")
    # Collect likely article links on this category page
    for a in soup.select("a[href]"):
      href = a.get("href", "").split("#")[0]
      if is_support_article(href):
        pages.append(href)
    # try a few common "next" selectors
    nxt = soup.select_one('a.next, a[rel="next"], .nav-previous a[rel="prev"]')
    next_url = nxt.get("href") if nxt and nxt.has_attr("href") else None
    time.sleep(SLEEP)
  # de-dup
  out, seen = [], set()
  for u in pages:
    if u not in seen:
      seen.add(u); out.append(u)
  return out

def slug_to_title(url: str) -> str:
  seg = url.rstrip("/").split("/")[-1]
  return seg.replace("-", " ").strip().title()

def extract_anchors(article_url: str):
  soup = BS(get(article_url).text, "html.parser")
  anchors = []
  # Headings with IDs are reliable anchors
  for h in soup.select("h2[id], h3[id], h4[id]"):
    aid = h.get("id")
    if aid:
      title = h.get_text(" ", strip=True) or f"#{aid}"
      anchors.append({"title": title, "url": f"{article_url}#{aid}"})
  # Some pages have TOC links that start with '#'
  for a in soup.select('a[href^="#"]'):
    frag = a.get("href", "").lstrip("#")
    if frag:
      title = a.get_text(" ", strip=True) or f"#{frag}"
      anchors.append({"title": title, "url": f"{article_url}#{frag}"})
  # de-dup by url
  out, seen = [], set()
  for a in anchors:
    if a["url"] not in seen:
      seen.add(a["url"]); out.append(a)
  page_title = extract_title_from_soup(soup) or slug_to_title(article_url)
  return out, page_title

def build_manifest():
  sections = []
  for cat_url in extract_categories():
    try:
      cat_soup = BS(get(cat_url).text, "html.parser")
      section_title = extract_title_from_soup(cat_soup) or slug_to_title(cat_url)
      page_urls = extract_pages(cat_url)

      pages = []
      for p in page_urls:
        try:
          anchors, title = extract_anchors(p)
          pages.append({"title": title, "url": p, "anchors": anchors})
          time.sleep(SLEEP)
        except Exception:
          # Skip a problematic page but keep going
          continue

      sections.append({"section": section_title, "url": cat_url, "pages": pages})
      time.sleep(SLEEP)
    except Exception:
      # Skip a problematic category but keep going
      continue

  manifest = {
    "version": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "source": ROOT,
    "sections": sections
  }
  return manifest

if __name__ == "__main__":
  data = build_manifest()
  with open("support-links.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
