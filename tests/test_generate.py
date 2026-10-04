"""Tests for the site generator. Network-free: repositories and icons are injected."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import generate as g  # noqa: E402

ORG = "LLM-Coding"


def repo(name, desc="", pages=True, archived=False, homepage=""):
    return {"name": name, "description": desc, "has_pages": pages,
            "archived": archived, "homepage": homepage,
            "html_url": f"https://github.com/{ORG}/{name}"}


def test_select_projects_filters_pages_archived_and_org_site():
    repos = [repo("b"), repo("a"), repo("no-pages", pages=False),
             repo("old", archived=True), repo("llm-coding.github.io")]
    names = [r["name"] for r in g.select_projects(repos, "llm-coding.github.io")]
    assert names == ["a", "b"]


def test_description_falls_back_to_overrides():
    assert g.description(repo("x", "From GitHub"), {"x": "Fallback"}) == "From GitHub"
    assert g.description(repo("x"), {"x": "Fallback"}) == "Fallback"
    assert g.description(repo("x"), {}) == ""


def test_find_icon_prefers_largest_apple_touch_icon():
    html = """<link rel="shortcut icon" href="favicon.ico">
    <link rel="apple-touch-icon" sizes="57x57" href="a57.png">
    <link rel="apple-touch-icon" sizes="180x180" href="a180.png">"""
    assert g.find_icons(html, "https://x.org/p/")[0] == "https://x.org/p/a180.png"


def test_find_icon_uses_rel_icon_and_falls_back_to_favicon_ico():
    html = '<link rel="icon" type="image/svg+xml" href="/p/favicon.svg" />'
    assert g.find_icons(html, "https://x.org/p/") == [
        "https://x.org/p/favicon.svg", "https://x.org/favicon.ico"]
    assert g.find_icons("<html></html>", "https://x.org/p/") == ["https://x.org/favicon.ico"]


def test_initial_icon_is_inline_svg_with_first_letter():
    svg = g.initial_icon("vibe-coding-risk-radar")
    assert svg.startswith("<svg") and ">V</text>" in svg


def test_shared_icons_are_dropped():
    icons = {"a": ("png", b"same", "u"), "b": ("png", b"same", "u"), "c": ("svg", b"own", "u")}
    assert g.drop_shared_icons(icons) == {"c": ("svg", b"own", "u")}


def project(name, desc="Desc", url=None, icon=None):
    return {"name": name, "description": desc,
            "url": url or f"https://llm-coding.github.io/{name}/",
            "repo_url": f"https://github.com/{ORG}/{name}", "icon": icon}


def test_index_lists_projects_statically_with_version():
    html = g.render_index([project("a", "Alpha <b>", icon="icons/a.png"), project("b")],
                          {"f": "https://x"}, "1.2.3", "2026-10-04")
    assert 'href="https://llm-coding.github.io/a/"' in html
    assert "Alpha &lt;b&gt;" in html
    assert 'src="icons/a.png"' in html
    assert ">B</text>" in html  # generated initial for b
    assert "1.2.3" in html and "<script" not in html


def test_llms_txt_lists_projects_and_shortlinks():
    txt = g.render_llms([project("a", "Alpha")], {"f": "https://t/"}, "1.0.0")
    assert "- [a](https://llm-coding.github.io/a/): Alpha" in txt
    assert "https://llm-coding.github.io/f/ -> https://t/" in txt


def test_redirect_page_works_without_js():
    html = g.render_redirect("f", "https://t/?a=1&b=2")
    assert '<meta http-equiv="refresh" content="0; url=https://t/?a=1&amp;b=2">' in html
    assert '<link rel="canonical" href="https://t/?a=1&amp;b=2">' in html
    assert '<a href="https://t/?a=1&amp;b=2">' in html and "<script" not in html


def test_shortlink_overview_is_a_static_table():
    html = g.render_shortlinks({"f": "https://t/"}, "1.0.0", "2026-10-04")
    assert "<table" in html and 'href="f/"' in html and "https://t/" in html


@pytest.mark.parametrize("links,err", [
    ({"Bad Key": "https://t/"}, "key"),
    ({"f": "javascript:alert(1)"}, "URL"),
    ({"a": "https://t/"}, "collides"),
    ({"icons": "https://t/"}, "reserved"),
])
def test_validate_links_rejects_bad_entries(links, err):
    with pytest.raises(ValueError, match=err):
        g.validate_links(links, ["a"])


def test_build_writes_all_files(tmp_path):
    projects = [project("a", icon=None)]
    g.write_site(tmp_path, projects, {}, {"f": "https://t/"}, "1.0.0", "2026-10-04")
    for f in ["index.html", "llms.txt", "sl.html", "f/index.html", "style.css", "favicon.svg", ".nojekyll"]:
        assert (tmp_path / f).exists(), f


def test_svg_icons_are_hotlinked_not_copied(tmp_path):
    """Review #1: a foreign SVG served from our origin could run scripts."""
    projects = [project("s"), project("p")]
    icons = {"s": ("svg", b"<svg/>", "https://ext.org/i.svg"),
             "p": ("png", b"\x89PNG", "https://ext.org/i.png")}
    g.write_site(tmp_path, projects, icons, {}, "1.0.0", "2026-10-04")
    assert not list((tmp_path / "icons").glob("*.svg"))
    assert (tmp_path / "icons" / "p.png").exists()
    html = (tmp_path / "index.html").read_text()
    assert 'src="https://ext.org/i.svg"' in html and 'src="icons/p.png"' in html


def test_fetch_refuses_non_http_urls():
    """Review #2: icon hrefs come from foreign HTML."""
    with pytest.raises(ValueError):
        g.fetch("file:///etc/passwd")


def test_pages_url_ignores_non_http_homepage(monkeypatch):
    """Review #3: a javascript: homepage must not become a card link."""
    def no_pages(path):
        raise g.subprocess.CalledProcessError(1, "gh")
    monkeypatch.setattr(g, "gh_api", no_pages)
    r = {"name": "x", "full_name": "LLM-Coding/x", "homepage": "javascript:alert(1)"}
    assert g.pages_url(r) == "https://llm-coding.github.io/x/"
