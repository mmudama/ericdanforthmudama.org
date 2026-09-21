"""
Add SEO metadata to the static site: absolute canonical URLs, meta
descriptions, Open Graph tags, image alt text, and a sitemap.xml.

Run once from the repo root after migrate.py. Safe to re-run: it recomputes
everything from the current page content each time (kept in the repo per the
same reasoning as migrate.py -- easy to tweak and re-run later).

Usage:
    python scripts/seo.py
"""
import glob
import html
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

SITE = "https://www.ericdanforthmudama.org"
SITE_NAME = "Eric Danforth Mudama"


def load(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, content):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def url_for(rel_path):
    if rel_path == "index.html":
        return SITE + "/"
    return f"{SITE}/{rel_path.replace(os.sep, '/')}"


def truncate(text, limit=160):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "..."


def esc(text):
    return html.escape(text, quote=True)


def photo_key(src):
    """Normalize an image src to a size-independent identity key."""
    src = src.split("/", src.count("../"))[-1] if src.startswith("../") else src
    src = re.sub(r"^\.\./+", "", src)
    base = os.path.basename(src)
    base = re.sub(r"-e?\d+x\d+(\.\w+)$", r"\1", base)  # strip -640x480 size suffix
    return os.path.dirname(src) + "/" + base


IMG_TAG_RE = re.compile(r"<img\b[^>]*>")
SRC_RE = re.compile(r'src="([^"]+)"')


def best_from_img_tag(tag):
    """Pick the largest available image from an <img>'s srcset, falling back to src."""
    srcset_m = re.search(r'srcset="([^"]+)"', tag)
    if srcset_m:
        candidates = []
        for part in srcset_m.group(1).split(","):
            pm = re.match(r"\s*(\S+)\s+(\d+)w\s*$", part)
            if pm:
                candidates.append((int(pm.group(2)), pm.group(1)))
        if candidates:
            return max(candidates, key=lambda c: c[0])[1]
    src_m = re.search(r'src="([^"]+)"', tag)
    return src_m.group(1) if src_m else None


def first_srcset_image(content):
    m = re.search(r'class="entry-media-wrap">\s*(<img[^>]*>)', content)
    if m:
        return best_from_img_tag(m.group(1))
    return None


def get_title(content):
    m = re.search(r"<title>(.*?)</title>", content, re.S)
    title = html.unescape(m.group(1)).strip() if m else ""
    return re.sub(r"\s*[–-]\s*Eric Danforth Mudama\s*$", "", title).strip()


def _clean_html_text(fragment):
    text = re.sub(r"<br\s*/?>", " ", fragment)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


# Junk auto-captions some cameras embed as EXIF and WordPress imports verbatim
# (e.g. "OLYMPUS DIGITAL CAMERA") -- not a real caption, don't use as one.
JUNK_CAPTION_RE = re.compile(r"^[A-Z0-9 ]+ DIGITAL CAMERA$")


def get_caption_text(content):
    """A photo's caption: a reader comment if present, else real body text
    the family/friend wrote directly into the post (some posts were authored
    that way instead of via a comment) -- excluding junk EXIF auto-captions."""
    m = re.search(
        r'<div class="comment-content">\s*(.*?)\s*</div>', content, re.S
    )
    if m:
        return _clean_html_text(m.group(1))

    m = re.search(
        r'<div class="entry-content">(.*?)</div>\s*(?:<footer|</article>)', content, re.S
    )
    if not m:
        return None
    body = re.sub(r"<img[^>]*>", "", m.group(1))
    text = _clean_html_text(body)
    if not text or JUNK_CAPTION_RE.match(text):
        return None
    return text


SEO_TAG_RE = re.compile(
    r'[ \t]*<meta (?:name="description"|property="og:[a-z_]+") content="[^"]*"/>\n?'
)


def strip_existing_seo_tags(content):
    """Idempotency: drop any meta tags a previous run of this script inserted,
    so re-running doesn't pile up duplicates."""
    return SEO_TAG_RE.sub("", content)


def insert_head_tags(content, description, og_type, page_url, og_image_url):
    content = strip_existing_seo_tags(content)
    tags = [f'<meta name="description" content="{esc(description)}"/>']
    tags.append(f'<meta property="og:site_name" content="{esc(SITE_NAME)}"/>')
    tags.append(f'<meta property="og:type" content="{og_type}"/>')
    tags.append(f'<meta property="og:url" content="{esc(page_url)}"/>')
    title_m = re.search(r"<title>(.*?)</title>", content, re.S)
    if title_m:
        tags.append(f'<meta property="og:title" content="{esc(html.unescape(title_m.group(1)))}"/>')
    tags.append(f'<meta property="og:description" content="{esc(description)}"/>')
    if og_image_url:
        tags.append(f'<meta property="og:image" content="{esc(og_image_url)}"/>')
    block = "\n" + "\n".join(tags)
    return re.sub(r"(<title>.*?</title>)", r"\1" + block, content, count=1, flags=re.S)


def fix_canonical(content, page_url):
    return re.sub(
        r'(<link rel="canonical" href=")[^"]*("/>)',
        lambda m: m.group(1) + page_url + m.group(2),
        content,
    )


def process_tribute(path):
    content = load(path)
    title = get_title(content)
    description = truncate(f"{title} — a tribute to Eric Danforth Mudama.")
    page_url = url_for(path)
    img = first_srcset_image(content)
    og_image = SITE + "/" + re.sub(r"^\.\./+", "", img) if img else None
    content = insert_head_tags(content, description, "article", page_url, og_image)
    content = fix_canonical(content, page_url)
    write(path, content)
    alt_entries = {}
    if img:
        alt_entries[photo_key(img)] = truncate(title, 125)
    return alt_entries


def process_photo(path):
    content = load(path)
    caption = get_caption_text(content)
    img = first_srcset_image(content)
    if caption:
        description = truncate(f"{caption} — in memory of Eric Danforth Mudama.")
        alt_text = truncate(caption, 125)
    else:
        description = "A photo shared in memory of Eric Danforth Mudama."
        alt_text = "Photo of Eric Danforth Mudama"
    page_url = url_for(path)
    og_image = SITE + "/" + re.sub(r"^\.\./+", "", img) if img else None
    content = insert_head_tags(content, description, "article", page_url, og_image)
    content = fix_canonical(content, page_url)
    write(path, content)
    alt_entries = {}
    if img:
        alt_entries[photo_key(img)] = alt_text
    return alt_entries


def process_obituary():
    path = "obituary.html"
    content = load(path)
    description = (
        "Obituary for Eric Danforth Mudama (1974–2018): devoted husband, son, and "
        "brother; MIT graduate and Principal Engineer at Intel."
    )
    page_url = url_for(path)
    img_m = re.search(r"<img\b[^>]*>", content)
    img_src = best_from_img_tag(img_m.group(0)) if img_m else None
    og_image = SITE + "/" + re.sub(r"^\.\./+", "", img_src) if img_src else None
    content = insert_head_tags(content, description, "profile", page_url, og_image)
    content = fix_canonical(content, page_url)
    write(path, content)
    alt_entries = {}
    if img_src:
        alt_entries[photo_key(img_src)] = "Eric Danforth Mudama"
    return alt_entries


def process_homepage():
    path = "index.html"
    content = load(path)
    description = (
        "In memoriam: Eric Danforth Mudama. Photos, memories, and tributes shared "
        "by his family and friends."
    )
    page_url = url_for(path)
    content = insert_head_tags(content, description, "website", page_url, None)
    # homepage had no canonical/shortlink originally; add one after <title> block tags
    canonical_tag = f'<link rel="canonical" href="{page_url}"/>\n'
    content = re.sub(r"(<meta property=\"og:description\"[^>]*>\n)", r"\1" + canonical_tag, content, count=1)
    write(path, content)


def apply_alt_text(alt_map):
    files = ["index.html", "obituary.html"] + glob.glob("tributes/*.html") + glob.glob("photos/*.html")
    changed = 0
    filled = 0
    missing = set()
    for f in files:
        content = load(f)

        def sub_img(m):
            nonlocal filled, missing
            tag = m.group(0)
            alt_m = re.search(r'alt="([^"]*)"', tag)
            if not alt_m:
                return tag
            src_m = SRC_RE.search(tag)
            if not src_m:
                return tag
            key = photo_key(src_m.group(1))
            alt = alt_map.get(key)
            if not alt:
                missing.add(key)
                alt = "Photo of Eric Danforth Mudama"
            if alt_m.group(1) == alt:
                return tag
            filled += 1
            return tag[: alt_m.start()] + f'alt="{esc(alt)}"' + tag[alt_m.end() :]

        new_content = IMG_TAG_RE.sub(sub_img, content)
        if new_content != content:
            write(f, new_content)
            changed += 1
    print(f"alt text: filled {filled} <img> tags across {changed} files")
    if missing:
        print(f"  ({len(missing)} images used a generic fallback, no direct caption/title match)")
        for k in sorted(missing):
            print("   -", k)


def build_sitemap():
    files = ["index.html", "obituary.html"] + sorted(glob.glob("tributes/*.html")) + sorted(glob.glob("photos/*.html"))
    urls = [url_for(f) for f in files]
    entries = "\n".join(f"  <url>\n    <loc>{esc(u)}</loc>\n  </url>" for u in urls)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )
    write("sitemap.xml", xml)
    print(f"sitemap.xml: {len(urls)} URLs")


def update_robots_txt():
    content = load("robots.txt")
    if "Sitemap:" not in content:
        content = content.rstrip("\n") + f"\nSitemap: {SITE}/sitemap.xml\n"
        write("robots.txt", content)


def main():
    alt_map = {}
    for f in sorted(glob.glob("tributes/*.html")):
        alt_map.update(process_tribute(f))
    for f in sorted(glob.glob("photos/*.html")):
        alt_map.update(process_photo(f))
    alt_map.update(process_obituary())
    process_homepage()
    apply_alt_text(alt_map)
    build_sitemap()
    update_robots_txt()
    print("done.")


if __name__ == "__main__":
    main()
