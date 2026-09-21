"""
One-off: rename the 3 photo pages whose real caption lived in the post body
(not a WordPress comment) from their filename-based slug to a caption-based
one, now that scripts/seo.py's get_caption_text() can see that caption.
Updates every reference (homepage grid, prev/next chains) by a global
basename swap, then re-runs seo.py (idempotent) to fix self-referential
canonical/OG tags and regenerate sitemap.xml.

Usage: python scripts/rename_captioned.py
"""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, "scripts")
import migrate  # noqa: E402
import seo  # noqa: E402

RENAMES = [
    "photos/img-4564-e1523144822691.html",
    "photos/30441305-10155728038849023-5942237603641163776-o.html",
    "photos/15443294-10210613929880182-4247021605849918097-o.html",
]


def main():
    mapping = {}
    for old_path in RENAMES:
        content = open(old_path, encoding="utf-8").read()
        caption = seo.get_caption_text(content)
        slug = migrate.slugify(caption)
        new_path = f"photos/{slug}.html"
        if os.path.exists(new_path):
            raise SystemExit(f"collision: {new_path} already exists")
        mapping[old_path] = new_path
        print(f"{old_path} -> {new_path}")

    files = ["index.html", "obituary.html"] + glob.glob("tributes/*.html") + glob.glob("photos/*.html")
    for old_path, new_path in mapping.items():
        old_base = os.path.basename(old_path)
        new_base = os.path.basename(new_path)
        for f in files:
            with open(f, encoding="utf-8") as fh:
                c = fh.read()
            if old_base in c:
                c = c.replace(old_base, new_base)
                with open(f, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(c)

    for old_path, new_path in mapping.items():
        os.rename(old_path, new_path)

    seo.main()

    result = subprocess.run([sys.executable, "scripts/migrate.py", "--check"], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0 or "BROKEN" in result.stdout:
        raise SystemExit("link check failed after rename")


if __name__ == "__main__":
    main()
