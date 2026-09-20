"""
One-shot migration: turn the raw WordPress mirror into a clean static site.

Reads the *.html?p=NNN.html / ?page_id=NNN.html mirror files plus wp-content/
wp-includes, and rewrites the whole site into:

    index.html, obituary.html, robots.txt
    tributes/<slug>.html   (quote/title posts)
    photos/<slug>.html     (photo posts, captioned or not)
    images/<year>/<month>/<file>
    assets/css/*, assets/js/*

See C:\\Users\\momud\\.claude\\plans\\hashed-toasting-storm.md for the full
rationale. Safe to re-run: it recomputes everything from the *source* files
each time, so tweak the rules below and re-run rather than hand-editing output.

Usage:
    python scripts/migrate.py            # do the migration
    python scripts/migrate.py --check    # only run the link-integrity checker
"""
import glob
import html
import os
import re
import shutil
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def long_path(p):
    """Escape Windows' 260-char MAX_PATH for the one over-long filename we still touch."""
    if os.name != "nt":
        return p
    ap = os.path.abspath(p)
    return ap if ap.startswith("\\\\?\\") else "\\\\?\\" + ap

DOMAIN_RE = r"https?://www\.ericdanforthmudama\.org"

# ---------------------------------------------------------------------------
# slug helpers
# ---------------------------------------------------------------------------

def slugify(text, maxlen=60):
    text = html.unescape(text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    if len(text) > maxlen:
        text = text[:maxlen].rsplit("-", 1)[0]
    return text or "untitled"


def image_basis(srcset_or_src):
    """Turn an image filename into a slug basis: strip size suffix, extension, pagespeed junk."""
    name = srcset_or_src.rsplit("/", 1)[-1]
    name = re.sub(r"^\d+x\d+x", "", name)  # leading WxHx pagespeed prefix
    name = re.sub(r"\.pagespeed\.\w+\.[^.]+(?=\.\w+$)", "", name)  # .pagespeed.ic.HASH before ext
    name = re.sub(r"\.(jpe?g|png|gif)$", "", name, flags=re.I)
    name = re.sub(r"-e?\d+x\d+$", "", name)  # trailing -640x480 size suffix
    return name


# ---------------------------------------------------------------------------
# 1. parse every post
# ---------------------------------------------------------------------------

def parse_posts():
    files = sorted(
        glob.glob("index.html?p=*.html") + glob.glob("index.html?page_id=*.html"),
        key=lambda f: int(re.search(r"(?:p|page_id)=(\d+)", f).group(1)),
    )
    posts = []
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            content = fh.read()

        m = re.search(r"(p|page_id)=(\d+)", f)
        kind_q, pid = m.group(1), m.group(2)

        title_m = re.search(r"<title>(.*?)</title>", content, re.S)
        title = html.unescape(title_m.group(1)).strip() if title_m else ""
        title = re.sub(r"\s*[\u2013-]\s*Eric Danforth Mudama\s*$", "", title).strip()
        if title == "Eric Danforth Mudama":
            title = ""

        img = re.search(r'class="entry-media-wrap">\s*<img[^>]*srcset="([^"]+)"', content)
        image_name = ""
        if img:
            first_candidate = img.group(1).split(",")[0].strip().rsplit(" ", 1)[0]
            image_name = image_basis(first_candidate)

        comments = []
        for cm in re.finditer(
            r'<b class="fn">(.*?)</b>.*?<time datetime="([^"]+)">.*?<div class="comment-content">\s*(.*?)\s*</div>',
            content, re.S,
        ):
            author = html.unescape(cm.group(1)).strip()
            dt = cm.group(2)
            text = re.sub(r"<[^>]+>", " ", cm.group(3))
            text = html.unescape(re.sub(r"\s+", " ", text)).strip()
            comments.append({"author": author, "date": dt, "text": text})

        posts.append({
            "file": f,
            "id": pid,
            "is_page": kind_q == "page_id",
            "title": title,
            "image_name": image_name,
            "comments": comments,
            "content": content,
        })
    return posts


# ---------------------------------------------------------------------------
# 2. classify + assign new paths
# ---------------------------------------------------------------------------

def assign_paths(posts):
    used = {}

    def unique(path):
        if path not in used:
            used[path] = 1
            return path
        used[path] += 1
        base, ext = os.path.splitext(path)
        return f"{base}-{used[path]}{ext}"

    # WordPress captured the Obituary twice under two different query forms
    # (?p=86 and ?page_id=86) with identical content. Keep the page_id copy as
    # the real obituary.html and make the ?p=86 copy a pure alias: no file of
    # its own, its id just resolves to the same target for link rewriting.
    page_ids = {p["id"] for p in posts if p["is_page"]}
    aliases = set()
    for p in posts:
        if not p["is_page"] and p["id"] in page_ids:
            p["kind"] = "duplicate-alias"
            aliases.add(p["id"])

    for p in posts:
        if p["is_page"]:
            p["new_path"] = "obituary.html"
            p["kind"] = "page"
            continue
        if p.get("kind") == "duplicate-alias":
            continue  # new_path assigned below, after the real page_id entry is processed
        if p["title"]:
            slug = slugify(p["title"])
            p["new_path"] = unique(f"tributes/{slug}.html")
            p["kind"] = "tribute"
        elif p["comments"]:
            slug = slugify(p["comments"][0]["text"])
            p["new_path"] = unique(f"photos/{slug}.html")
            p["kind"] = "photo-captioned"
        else:
            slug = slugify(p["image_name"]) if p["image_name"] else p["id"]
            p["new_path"] = unique(f"photos/{slug}.html")
            p["kind"] = "photo"

    for p in posts:
        if p["kind"] == "duplicate-alias":
            p["new_path"] = "obituary.html"
    return {p["id"]: p for p in posts}


# ---------------------------------------------------------------------------
# 3. image dedup map
# ---------------------------------------------------------------------------

def build_image_map():
    plain = {}
    pagespeed = {}
    for path in glob.glob("wp-content/uploads/**/*", recursive=True):
        if not os.path.isfile(path):
            continue
        suffix = path.replace("\\", "/")
        if ".pagespeed." in suffix:
            pagespeed[suffix] = path
        else:
            plain[suffix] = path

    image_map = {}   # old suffix (wp-content/uploads/...) -> new relative path (images/...)
    for suffix in plain:
        new_path = "images/" + suffix[len("wp-content/uploads/"):]
        image_map[suffix] = new_path

    exceptions = {
        "wp-content/uploads/2018/03/640x480xmom_eric_me_dad2-640x480.jpg.pagespeed.ic.N8vLaA40hs.jpg":
            "images/2018/03/mom_eric_me_dad2-640x480.jpg",
        "wp-content/uploads/2018/03/600x800xhugondock_3334975442_o-600x800.jpg.pagespeed.ic.Qs2633PwwN.jpg":
            "images/2018/03/hugondock_3334975442_o-600x800.jpg",
    }

    for suffix, src_path in pagespeed.items():
        if suffix in exceptions:
            image_map[suffix] = exceptions[suffix]
            continue
        m = re.match(r"^(wp-content/uploads/.+/)\d+x\d+x(.+?)\.pagespeed\.\w+\.[^.]+\.(?:jpe?g|png|gif)$", suffix, re.I)
        if not m:
            continue
        # group(2) already includes the original extension (e.g. "...-1200x675.jpg")
        plain_suffix = f"{m.group(1)}{m.group(2)}"
        if plain_suffix in image_map:
            image_map[suffix] = image_map[plain_suffix]
        # else: unreferenced/no-sibling pagespeed file outside the known exceptions -> dropped

    return image_map, exceptions


# ---------------------------------------------------------------------------
# 4. asset map (theme/includes CSS+JS, plus the one combined bundle)
# ---------------------------------------------------------------------------

ASSET_MAP = {
    "wp-content/themes/altofocus-wpcom/A.style.css,qver=5.4.22.pagespeed.cf.-GlkgdDp1o.css":
        "assets/css/style.css",
    "wp-includes/css/dist/block-library/A.style.min.css,qver=5.4.22.pagespeed.cf._93gOJAMuK.css":
        "assets/css/block-library.css",
    "wp-includes/js/jquery/jquery.js,qver=1.12.4-wp.pagespeed.jm.gp20iU5FlU.js":
        "assets/js/jquery.js",
    "wp-includes/js/jquery/jquery-migrate.min.js,qver=1.4.1.pagespeed.jm.C2obERNcWh.js":
        "assets/js/jquery-migrate.js",
    "wp-content/themes/altofocus-wpcom/assets/js/isotope.pkgd.js,qver=3.0.1.pagespeed.jm.3rmvUeWfe3.js":
        "assets/js/isotope.js",
    "wp-content/themes/altofocus-wpcom/assets/js/jquery.flexslider.js,qver==2.6.1+columnlist.js,qver==20151120.pagespeed.jc.uQ4bHYo5EM.js":
        "assets/js/flexslider-columnlist.js",
    "wp-content/themes/altofocus-wpcom/assets/js/navigation.js,qver==20170301+imagesloaded.pkgd.js,qver==4.1.0.pagespeed.jc.XZ90vO3wvg.js":
        "assets/js/navigation-imagesloaded.js",
}

# The combined bundle: source file on disk has a truncated identifier (Windows
# path-length casualty at mirror time); the homepage references the full,
# untruncated identifier, which never matched any file even before this
# migration. Both string forms are mapped to the same new asset.
SITE_BUNDLE_SRC = (
    "wp-content,_themes,_altofocus-wpcom,_assets,_js,_grid.js,qver==20170301"
    "+wp-content,_themes,_altofocus-wpcom,_assets,_js,_scripts.js,qver==20170301"
    "+wp-content,_themes,_altofocus-wpcom,_assets,_js,_skip-link-focus-fix.js,qver==20170301"
    "+wp"
)
SITE_BUNDLE_FULL_REF = SITE_BUNDLE_SRC + "-includes,_js,_wp-embed.min.js,qver==5.4.22.pagespeed.jc._vX-60jevJ.js"
SITE_BUNDLE_NEW = "assets/js/site-bundle.js"


# ---------------------------------------------------------------------------
# 5. content rewriting
# ---------------------------------------------------------------------------

DROP_LINK_PATTERNS = [
    r"[ \t]*<link[^>]*rel=[\"']alternate[\"'][^>]*type=[\"']application/rss\+xml[\"'][^>]*/?>[ \t]*\n?",
    r"[ \t]*<link[^>]*rel=[\"']https://api\.w\.org/[\"'][^>]*/?>[ \t]*\n?",
    r"[ \t]*<link[^>]*rel=[\"']EditURI[\"'][^>]*/?>[ \t]*\n?",
    r"[ \t]*<link[^>]*rel=[\"']wlwmanifest[\"'][^>]*/?>[ \t]*\n?",
    r"[ \t]*<link[^>]*rel=[\"']alternate[\"'][^>]*type=[\"']application/json\+oembed[\"'][^>]*/?>[ \t]*\n?",
    r"[ \t]*<link[^>]*rel=[\"']alternate[\"'][^>]*type=[\"']text/xml\+oembed[\"'][^>]*/?>[ \t]*\n?",
    r"[ \t]*<link[^>]*rel=[\"']profile[\"'][^>]*>[ \t]*\n?",
    r"[ \t]*<link[^>]*rel=['\"]dns-prefetch['\"][^>]*href=['\"]//?s\.w\.org/?['\"][^>]*/?>[ \t]*\n?",
]
EMOJI_SCRIPT_RE = re.compile(
    r'[ \t]*<script type="text/javascript">window\._wpemojiSettings=.*?</script>[ \t]*\n?',
    re.S,
)

# href/src to the site root with nothing else after it
ROOT_LINK_RE = re.compile(DOMAIN_RE + r"/(?=[\"'])")
# the mirror's own already-relative homepage links (e.g. href="index.html")
BARE_INDEX_RE = re.compile(r'(?<=["\'])index\.html(?=["\'#])')

# post/page permalink. Seen in the wild in three shapes:
#   http://www.ericdanforthmudama.org/?p=461            (head <link rel=prev/next/canonical/shortlink>)
#   http://www.ericdanforthmudama.org/index.html?p=NNN  (absolute body links)
#   index.html%3Fp=304.html                              (mirror's relative %3F-encoded body links)
# domain prefix and the "index.html/php" segment are each independently optional.
PERMALINK_RE = re.compile(
    r"(?:" + DOMAIN_RE + r"/)?(?:index\.(?:html|php))?(?:\?|%3F)(p|page_id)=(\d+)(?:\.html)?((?:#[\w-]+)?)"
)

IMAGE_RE = re.compile(r"(?:" + DOMAIN_RE + r")?/?(wp-content/uploads/[^\"'\s]+)")
ASSET_RE = re.compile(r"(?:" + DOMAIN_RE + r")?/?(wp-content/themes/[^\"'\s]+|wp-includes/[^\"'\s]+)")


def new_dir_of(new_path):
    return os.path.dirname(new_path)


def relpath(new_path_target, from_dir):
    rel = os.path.relpath(new_path_target, from_dir or ".")
    return rel.replace("\\", "/")


def rewrite(content, current_new_path, by_id, image_map):
    current_dir = new_dir_of(current_new_path)

    for pat in DROP_LINK_PATTERNS:
        content = re.sub(pat, "", content)
    content = EMOJI_SCRIPT_RE.sub("", content)

    content = ROOT_LINK_RE.sub(lambda m: relpath("index.html", current_dir), content)
    content = BARE_INDEX_RE.sub(lambda m: relpath("index.html", current_dir), content)

    def perma_sub(m):
        kind_q, pid, frag = m.group(1), m.group(2), m.group(3)
        target = by_id.get(pid)
        if not target:
            return m.group(0)  # leave unrecognized ids untouched, surfaced by checker
        return relpath(target["new_path"], current_dir) + frag

    content = PERMALINK_RE.sub(perma_sub, content)

    def image_sub(m):
        suffix = m.group(1)
        new_target = image_map.get(suffix)
        if not new_target:
            return m.group(0)
        return relpath(new_target, current_dir)

    content = IMAGE_RE.sub(image_sub, content)

    def asset_sub(m):
        suffix = m.group(1)
        new_target = ASSET_MAP.get(suffix)
        if not new_target:
            return m.group(0)
        return relpath(new_target, current_dir)

    content = ASSET_RE.sub(asset_sub, content)

    bundle_new = relpath(SITE_BUNDLE_NEW, current_dir)
    # domain-prefixed forms first (some pages embed the bundle URL with the
    # absolute domain still attached), then bare relative forms
    content = re.sub(DOMAIN_RE + r"/" + re.escape(SITE_BUNDLE_FULL_REF), bundle_new, content)
    content = re.sub(DOMAIN_RE + r"/" + re.escape(SITE_BUNDLE_SRC), bundle_new, content)
    content = content.replace(SITE_BUNDLE_FULL_REF, bundle_new)
    content = content.replace(SITE_BUNDLE_SRC, bundle_new)

    content = content.replace("http://fonts.googleapis.com", "https://fonts.googleapis.com")
    content = content.replace("//fonts.googleapis.com", "https://fonts.googleapis.com")

    return content


# ---------------------------------------------------------------------------
# 6. homepage / misc top-level rewriting (same rules, just not "posts")
# ---------------------------------------------------------------------------

def load(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

DROP_FILES_GLOBS = [
    "index.html?feed=*",
    "index.php?rest_route=%2F",
    "index.php?rest_route=%2Foembed%2F1.0%2Fembed&url=*",
    "xmlrpc.php?rsd",
    "wp-includes/wlwmanifest.xml",
    "robots.txt.html",
]


def do_migrate():
    posts = parse_posts()
    by_id = assign_paths(posts)
    image_map, exceptions = build_image_map()

    print(f"posts parsed: {len(posts)}")
    kinds = {}
    for p in posts:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    print("by kind:", kinds)

    # --- write post/page pages ---
    for p in posts:
        if p.get("kind") == "duplicate-alias":
            continue
        new_content = rewrite(p["content"], p["new_path"], by_id, image_map)
        write(p["new_path"], new_content)

    # --- homepage ---
    home_content = rewrite(load("index.html"), "index.html", by_id, image_map)
    write("index.html.new", home_content)  # write to temp name first, swap after old files removed

    # --- robots.txt (source was a bogus duplicate homepage capture; write a fresh one) ---
    write("robots.txt", "User-agent: *\nAllow: /\n")

    # --- images ---
    copied = 0
    for suffix, new_path in image_map.items():
        src = suffix
        if not os.path.exists(new_path):
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            shutil.copyfile(src, new_path)
            copied += 1
    print(f"images copied: {copied} (of {len(image_map)} mapped)")

    # --- assets ---
    for suffix, new_path in ASSET_MAP.items():
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        shutil.copyfile(suffix, new_path)
    # combined bundle: source file is the truncated-name one on disk, sitting
    # at the repo root (not under wp-content/wp-includes)
    bundle_src = SITE_BUNDLE_SRC
    os.makedirs(os.path.dirname(SITE_BUNDLE_NEW), exist_ok=True)
    shutil.copyfile(long_path(bundle_src), SITE_BUNDLE_NEW)
    os.remove(long_path(bundle_src))
    print(f"assets copied: {len(ASSET_MAP) + 1}")

    # --- remove old post files ---
    for p in posts:
        os.remove(p["file"])

    # --- swap homepage into place ---
    os.remove("index.html")
    os.rename("index.html.new", "index.html")

    # --- drop pure junk ---
    dropped = 0
    for pattern in DROP_FILES_GLOBS:
        for f in glob.glob(pattern):
            if os.path.isfile(f):
                os.remove(f)
                dropped += 1
    print(f"junk files dropped: {dropped}")

    # --- remove old wp-content / wp-includes trees entirely (everything useful was copied out) ---
    shutil.rmtree("wp-content")
    shutil.rmtree("wp-includes")

    print("done.")
    return by_id, image_map


# ---------------------------------------------------------------------------
# link-integrity checker
# ---------------------------------------------------------------------------

LOCAL_REF_RE = re.compile(r'(?:href|src)="([^"]+)"')


def check_links():
    problems = []
    html_files = glob.glob("*.html") + glob.glob("tributes/*.html") + glob.glob("photos/*.html")
    for f in html_files:
        content = load(f)
        base_dir = os.path.dirname(f)
        for m in LOCAL_REF_RE.finditer(content):
            ref = m.group(1)
            if not ref or ref.startswith(("http://", "https://", "//", "#", "mailto:")):
                continue
            path_part = ref.split("#", 1)[0]
            if not path_part:
                continue
            resolved = os.path.normpath(os.path.join(base_dir, path_part))
            if not os.path.exists(resolved):
                problems.append((f, ref, resolved))
    if problems:
        print(f"BROKEN LINKS: {len(problems)}")
        for f, ref, resolved in problems[:50]:
            print(f"  {f} -> {ref}  (resolved: {resolved})")
    else:
        print(f"OK: no broken local links across {len(html_files)} html files")
    return problems


if __name__ == "__main__":
    if "--check" in sys.argv:
        check_links()
    else:
        do_migrate()
        check_links()
