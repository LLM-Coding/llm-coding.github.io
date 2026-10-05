"""Tests for the display order and the extra (non-repository) cards from overrides.yaml."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import generate as g  # noqa: E402

LAYOUT = """\
order: [b, wheel, a]
extras:
  wheel:
    name: Harness Wheel
    url: https://x.org/wheel.html
    description: "An interactive wheel: try it."
a:
  name: A
"""


def card(name, repo_url="https://github.com/LLM-Coding/x"):
    return {"name": name, "title": name, "description": "D",
            "url": f"https://x.org/{name}/", "repo_url": repo_url, "icon": None}


def test_load_overrides_ignores_order_and_extras(tmp_path):
    f = tmp_path / "o.yaml"
    f.write_text(LAYOUT)
    assert g.load_overrides(f) == {"a": {"name": "A"}}


def test_load_layout_reads_order_and_extras(tmp_path):
    f = tmp_path / "o.yaml"
    f.write_text(LAYOUT)
    order, extras = g.load_layout(f)
    assert order == ["b", "wheel", "a"]
    assert extras == {"wheel": {"name": "Harness Wheel", "url": "https://x.org/wheel.html",
                                "description": "An interactive wheel: try it."}}


def test_load_layout_without_fields(tmp_path):
    f = tmp_path / "o.yaml"
    f.write_text("a:\n  name: A\n")
    assert g.load_layout(f) == ([], {})
    assert g.load_layout(tmp_path / "missing.yaml") == ([], {})


def test_extra_cards_become_projects_without_repository():
    extras = {"wheel": {"name": "Harness Wheel", "url": "https://x.org/w.html",
                        "description": "Wheel"}}
    assert g.extra_projects(extras) == [{"name": "wheel", "title": "Harness Wheel",
                                         "description": "Wheel", "url": "https://x.org/w.html",
                                         "repo_url": None, "icon": None}]


def test_order_puts_listed_first_then_rest_alphabetically():
    cards = [card("a"), card("B-new"), card("b"), card("c"), card("wheel", None)]
    ordered = g.order_projects(cards, ["b", "wheel", "a"], ["a", "b", "B-new", "c", "wheel"])
    assert [p["name"] for p in ordered] == ["b", "wheel", "a", "B-new", "c"]


def test_order_matches_repository_names_case_insensitively():
    ordered = g.order_projects([card("a"), card("Semantic-Anchors")],
                               ["semantic-anchors"], ["a", "Semantic-Anchors"])
    assert [p["name"] for p in ordered] == ["Semantic-Anchors", "a"]


def test_order_skips_known_but_unlisted_repos():
    """An archived repo in the order list must not break the daily build."""
    ordered = g.order_projects([card("a")], ["old", "a"], ["a", "old"])
    assert [p["name"] for p in ordered] == ["a"]


@pytest.mark.parametrize("order", [["nope"], ["a", "a"]])
def test_order_rejects_unknown_or_duplicate_keys(order):
    with pytest.raises(ValueError, match="order"):
        g.order_projects([card("a")], order, ["a"])


@pytest.mark.parametrize("extras,err", [
    ({"Bad Key": {"name": "X", "url": "https://t/"}}, "key"),
    ({"a": {"name": "X", "url": "https://t/"}}, "repository"),
    ({"A": {"name": "X", "url": "https://t/"}}, "key"),
    ({"f": {"name": "X", "url": "https://t/"}}, "shortlink"),
    ({"icons": {"name": "X", "url": "https://t/"}}, "reserved"),
    ({"w": {"name": "X", "url": "javascript:alert(1)"}}, "URL"),
    ({"w": {"name": "X"}}, "URL"),
])
def test_validate_extras_rejects_bad_entries(extras, err):
    with pytest.raises(ValueError, match=err):
        g.validate_extras(extras, ["a"], {"f": "https://t/"})


def test_validate_extras_rejects_repo_name_in_other_case():
    with pytest.raises(ValueError, match="repository"):
        g.validate_extras({"semantic-anchors": {"name": "X", "url": "https://t/"}},
                          ["Semantic-Anchors"], {})


def test_extra_card_has_no_repository_link_and_no_label():
    html = g.render_index([card("wheel", None)], {}, "1", "d")
    assert "Repository" not in html and "None" not in html
    assert 'href="https://x.org/wheel/"' in html
    assert "external" not in html.lower() and "prototype" not in html.lower()


def test_repo_card_keeps_repository_link():
    assert ">Repository</a>" in g.render_index([card("a")], {}, "1", "d")
