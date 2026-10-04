#!/usr/bin/env python3
"""Generate the static LLM-Coding org site: project overview, llms.txt and shortlinks.

Data comes from the GitHub API via the `gh` CLI. Output goes to `_site/`.
Usage: python3 generate.py [--out _site] [--org LLM-Coding]
"""
import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yaml

ROOT = Path(__file__).resolve().parent
SITE_URL = "https://llm-coding.github.io/"
ORG_SITE_REPO = "llm-coding.github.io"
RESERVED_KEYS = {"icons", "sl", "index", "llms", "style"}
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ICON_TYPES = {"image/png": "png", "image/svg+xml": "svg", "image/x-icon": "ico",
              "image/vnd.microsoft.icon": "ico", "image/jpeg": "jpg", "image/webp": "webp"}
USER_AGENT = "llm-coding-site-generator"
esc = html.escape


# --- data -----------------------------------------------------------------

def gh_api(path):
    out = subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def gh_api_list(path):
    out = subprocess.run(["gh", "api", "--paginate", path, "--jq", ".[]"], check=True,
                         capture_output=True, text=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def pages_url(repo):
    try:
        url = gh_api(f"repos/{repo['full_name']}/pages").get("html_url")
    except (subprocess.CalledProcessError, AttributeError):
        url = None
    homepage = repo.get("homepage") or ""
    if not is_http(homepage):
        homepage = ""
    return url or homepage or f"{SITE_URL}{repo['name']}/"


def is_http(url):
    return urlparse(url).scheme in ("http", "https")


def select_projects(repos, overrides, org_site_repo=ORG_SITE_REPO):
    """Repos with Pages, minus archived, the org site, excluded ones and forks (unless included)."""
    def wanted(r):
        ov = overrides.get(r["name"], {})
        return (r.get("has_pages") and not r.get("archived")
                and r["name"].lower() != org_site_repo.lower()
                and not ov.get("exclude") and (not r.get("fork") or ov.get("include")))
    chosen = [r for r in repos if wanted(r)]
    return sorted(chosen, key=lambda r: r["name"].lower())


def display_name(repo, overrides):
    return overrides.get(repo["name"], {}).get("name") or repo["name"]


def description(repo, overrides):
    return (overrides.get(repo["name"], {}).get("description")
            or (repo.get("description") or "").strip())


def load_overrides(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    result = {}
    for key, v in (data or {}).items():
        v = v or {}
        entry = {f: str(v[f]) for f in ("name", "description") if v.get(f)}
        entry.update({f: True for f in ("include", "exclude") if v.get(f) is True})
        result[str(key)] = entry
    return result


def load_yaml(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    return {str(k): str(v) for k, v in (data or {}).items()}


def validate_links(links, repo_names):
    names = {n.lower() for n in repo_names}
    for key, url in links.items():
        if not KEY_RE.match(key):
            raise ValueError(f"Invalid shortlink key {key!r}: use a-z, 0-9 and '-'")
        if key in RESERVED_KEYS:
            raise ValueError(f"Shortlink key {key!r} is reserved")
        if key in names:
            raise ValueError(f"Shortlink key {key!r} collides with a repository page")
        if not is_http(url):
            raise ValueError(f"Shortlink {key!r} needs an http(s) URL, got {url!r}")


# --- icons ----------------------------------------------------------------

class _IconParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.apple, self.icons = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag != "link" or not a.get("href"):
            return
        rel = (a.get("rel") or "").lower().split()
        size = max((int(s.split("x")[0]) for s in (a.get("sizes") or "").split()
                    if re.match(r"^\d+x\d+$", s)), default=0)
        if "apple-touch-icon" in rel:
            self.apple.append((size, a["href"]))
        elif "icon" in rel:
            self.icons.append((size, a["href"]))


def find_icons(page_html, base_url):
    """Icon URL candidates, best first: largest apple-touch-icon, rel=icon, /favicon.ico."""
    p = _IconParser()
    p.feed(page_html)
    hrefs = [h for _, h in sorted(p.apple, key=lambda x: -x[0])]
    hrefs += [h for _, h in sorted(p.icons, key=lambda x: -x[0])]
    urls = [urljoin(base_url, h) for h in hrefs] + [urljoin(base_url, "/favicon.ico")]
    return list(dict.fromkeys(urls))


def fetch(url, timeout=15):
    if not is_http(url):
        raise ValueError(f"Refusing non-http(s) URL {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.headers.get_content_type(), resp.read()


def fetch_icon(site_url):
    """Return (extension, bytes, url) of the site's icon, or None."""
    try:
        _, page = fetch(site_url)
        candidates = find_icons(page.decode("utf-8", "replace"), site_url)
    except Exception as e:  # noqa: BLE001 — a dead site must not break the build
        print(f"  ! {site_url}: {e}", file=sys.stderr)
        candidates = [urljoin(site_url, "/favicon.ico")]
    for url in candidates:
        try:
            ctype, data = fetch(url)
        except Exception:  # noqa: BLE001
            continue
        ext = ICON_TYPES.get(ctype) or Path(urlparse(url).path).suffix.lstrip(".").lower()
        if data and ext in ICON_TYPES.values():
            return ext, data, url
    return None


def drop_shared_icons(icons):
    """Drop icons used by several sites (e.g. the docToolchain default) — they tell nothing apart."""
    digests = {}
    for name, (_, data, *_) in icons.items():
        digests.setdefault(hashlib.sha256(data).hexdigest(), []).append(name)
    shared = {n for names in digests.values() if len(names) > 1 for n in names}
    return {n: v for n, v in icons.items() if n not in shared}


def initial_icon(name):
    hue = int(hashlib.sha256(name.encode()).hexdigest(), 16) % 360
    letter = esc(next((c for c in name if c.isalnum()), "?").upper())
    return (f'<svg class="icon" viewBox="0 0 64 64" width="48" height="48" aria-hidden="true">'
            f'<rect width="64" height="64" rx="14" fill="hsl({hue} 55% 45%)"/>'
            f'<text x="32" y="43" text-anchor="middle" font-size="32" font-weight="600" '
            f'font-family="system-ui, sans-serif" fill="#fff">{letter}</text></svg>')


# --- rendering ------------------------------------------------------------

def page(title, body, version, generated, description_text=""):
    meta = f'<meta name="description" content="{esc(description_text)}">\n' if description_text else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
{meta}<link rel="stylesheet" href="/style.css">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="alternate" type="text/plain" href="/llms.txt" title="llms.txt">
</head>
<body>
<header><a class="brand" href="/">LLM-Coding</a>
<nav><a href="/">Projects</a> <a href="/sl.html">Shortlinks</a> <a href="https://github.com/LLM-Coding">GitHub</a></nav>
</header>
<main>
{body}
</main>
<footer>Version {esc(version)} · generated {esc(generated)} ·
<a href="/llms.txt">llms.txt</a> ·
<a href="https://github.com/LLM-Coding/{ORG_SITE_REPO}">Source</a></footer>
</body>
</html>
"""


def render_index(projects, links, version, generated):
    items = []
    for p in projects:
        icon = (f'<img class="icon" src="{esc(p["icon"])}" alt="" width="48" height="48">'
                if p.get("icon") else initial_icon(p["name"]))
        items.append(f"""<li class="card">
{icon}
<div><h2><a href="{esc(p["url"])}">{esc(p.get("title") or p["name"])}</a></h2>
<p>{esc(p["description"])}</p>
<p class="meta"><a href="{esc(p["url"])}">{esc(p["url"])}</a> · <a href="{esc(p["repo_url"])}">Repository</a></p></div>
</li>""")
    body = f"""<h1>LLM-Coding projects</h1>
<p class="lead">Open projects about software development with large language models.
Each one has its own website.</p>
<ul class="projects">
{chr(10).join(items)}
</ul>
<p>{len(links)} shortlink{"s" if len(links) != 1 else ""}: <a href="/sl.html">overview</a>.</p>"""
    return page("LLM-Coding – projects", body, version, generated,
                "Overview of all LLM-Coding projects with their websites.")


def render_llms(projects, links, version):
    lines = ["# LLM-Coding", "",
             "> Open projects about software development with large language models.", "",
             f"Version {version}. Source: https://github.com/LLM-Coding/{ORG_SITE_REPO}", "",
             "## Projects", ""]
    lines += [f"- [{p.get('title') or p['name']}]({p['url']}): {p['description']}"
              for p in projects]
    lines += ["", "## Shortlinks", ""]
    lines += [f"- {SITE_URL}{k}/ -> {u}" for k, u in links.items()]
    return "\n".join(lines) + "\n"


def render_redirect(key, url):
    u = esc(url)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0; url={u}">
<link rel="canonical" href="{u}">
<meta name="robots" content="noindex">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Redirecting to {u}</title>
<link rel="stylesheet" href="/style.css">
</head>
<body>
<main><p>Redirecting to <a href="{u}">{u}</a> …</p></main>
</body>
</html>
"""


def render_shortlinks(links, version, generated):
    rows = "\n".join(
        f'<tr><td><a href="{esc(k)}/">{esc(SITE_URL)}{esc(k)}</a></td>'
        f'<td><a href="{esc(u)}">{esc(u)}</a></td></tr>' for k, u in sorted(links.items()))
    body = f"""<h1>Shortlinks</h1>
<p class="lead">Short addresses for slides and QR codes.</p>
<div class="table-wrap"><table>
<thead><tr><th>Shortlink</th><th>Target</th></tr></thead>
<tbody>
{rows}
</tbody>
</table></div>"""
    return page("LLM-Coding – shortlinks", body, version, generated)


# --- build ----------------------------------------------------------------

def write_site(out, projects, icons, links, version, generated):
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    (out / "icons").mkdir(parents=True)
    for p in projects:
        if p["name"] in icons:
            ext, data, url = icons[p["name"]]
            if ext == "svg":
                # A foreign SVG served from our origin could run scripts — hotlink it instead
                p["icon"] = url
            else:
                p["icon"] = f"icons/{p['name']}.{ext}"
                (out / p["icon"]).write_bytes(data)
    (out / "index.html").write_text(render_index(projects, links, version, generated), "utf-8")
    (out / "llms.txt").write_text(render_llms(projects, links, version), "utf-8")
    (out / "sl.html").write_text(render_shortlinks(links, version, generated), "utf-8")
    for key, url in links.items():
        (out / key).mkdir()
        (out / key / "index.html").write_text(render_redirect(key, url), "utf-8")
    for asset in ("style.css", "favicon.svg"):
        shutil.copy(ROOT / "static" / asset, out / asset)
    (out / ".nojekyll").write_text("")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "_site"))
    ap.add_argument("--org", default="LLM-Coding")
    args = ap.parse_args()

    all_repos = gh_api_list(f"orgs/{args.org}/repos?per_page=100")
    overrides = load_overrides(ROOT / "overrides.yaml")
    repos = select_projects(all_repos, overrides)
    links = load_yaml(ROOT / "links.yaml")
    # Archived repos keep serving their Pages, so check against every repo name
    validate_links(links, [r["name"] for r in all_repos])

    projects, icons = [], {}
    for r in repos:
        url = pages_url(r)
        print(f"{r['name']}: {url}")
        projects.append({"name": r["name"], "title": display_name(r, overrides),
                         "description": description(r, overrides),
                         "url": url, "repo_url": r["html_url"], "icon": None})
        icon = fetch_icon(url)
        if icon:
            icons[r["name"]] = icon
    icons = drop_shared_icons(icons)

    version = (ROOT / "VERSION").read_text().strip()
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    write_site(args.out, projects, icons, links, version, generated)
    print(f"Wrote {len(projects)} projects and {len(links)} shortlinks to {args.out}")


if __name__ == "__main__":
    main()
