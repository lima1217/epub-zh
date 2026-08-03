from __future__ import annotations

from lxml import etree

from epub_zh.translate import apply_translations, collect_blocks


def parse_xhtml_from_string(html: str) -> etree._Element:
    parser = etree.XMLParser(recover=True, resolve_entities=False)
    return etree.fromstring(html.encode("utf-8"), parser=parser)


def test_apply_zh_preserves_toc_nested_list_and_link() -> None:
    html = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
  <nav>
    <ol>
      <li class="toc-chap">
        <a href="b.xhtml">Preface to the Second Edition</a>
        <ol>
          <li class="toc-sect"><a href="c.xhtml">How the Book Is Organized</a></li>
        </ol>
      </li>
    </ol>
  </nav>
</body>
</html>
"""
    root = parse_xhtml_from_string(html)
    units = collect_blocks(root)
    assert [u.source_text for u in units] == [
        "Preface to the Second Edition",
        "How the Book Is Organized",
    ]
    apply_translations(units, ["第二版序言", "本书如何组织"], "zh")

    chap = root.find(".//{http://www.w3.org/1999/xhtml}li[@class='toc-chap']")
    assert chap is not None
    link = chap.find("{http://www.w3.org/1999/xhtml}a")
    assert link is not None
    assert link.get("href") == "b.xhtml"
    assert (link.text or "").strip() == "第二版序言"

    nested = chap.find("{http://www.w3.org/1999/xhtml}ol")
    assert nested is not None
    sect = nested.find("{http://www.w3.org/1999/xhtml}li")
    assert sect is not None
    # Leaf units still flatten to a single text node (pre-existing apply behavior).
    assert (sect.text or "").strip() == "本书如何组织"


def test_apply_zh_preserves_section_under_lead_in_div() -> None:
    html = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
  <div>Lead-in text<section><p>Inside</p></section></div>
</body>
</html>
"""
    root = parse_xhtml_from_string(html)
    units = collect_blocks(root)
    assert [u.source_text for u in units] == ["Lead-in text", "Inside"]
    apply_translations(units, ["引入文字", "内部段落"], "zh")

    div = root.find(".//{http://www.w3.org/1999/xhtml}div")
    assert div is not None
    assert (div.text or "").strip() == "引入文字"
    section = div.find("{http://www.w3.org/1999/xhtml}section")
    assert section is not None
    p = section.find("{http://www.w3.org/1999/xhtml}p")
    assert p is not None
    assert (p.text or "").strip() == "内部段落"


def test_apply_zh_still_replaces_leaf_paragraph() -> None:
    html = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
  <p>Hello <em>world</em></p>
</body>
</html>
"""
    root = parse_xhtml_from_string(html)
    units = collect_blocks(root)
    apply_translations(units, ["你好世界"], "zh")
    p = root.find(".//{http://www.w3.org/1999/xhtml}p")
    assert p is not None
    assert (p.text or "").strip() == "你好世界"
    assert list(p) == []
