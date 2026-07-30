from __future__ import annotations

from lxml import etree

from epub_zh.translate import collect_blocks


def test_collect_blocks_skips_paragraphs_inside_code_div() -> None:
    html = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
  <p>Prose to translate.</p>
  <div class="code">
    <p>try {</p>
    <p>return null;</p>
    <p>* javadoc line one</p>
    <p>* javadoc line two</p>
  </div>
  <p>More prose.</p>
</body>
</html>
"""
    root = parse_xhtml_from_string(html)
    units = collect_blocks(root)
    texts = [u.source_text for u in units]
    assert texts == ["Prose to translate.", "More prose."]


def test_collect_blocks_skips_pre_code_tags() -> None:
    html = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
  <p>Intro</p>
  <pre><code>fn main() {}</code></pre>
  <p>Outro</p>
</body>
</html>
"""
    root = parse_xhtml_from_string(html)
    assert [u.source_text for u in collect_blocks(root)] == ["Intro", "Outro"]


def test_collect_blocks_skips_leaf_code_class_blocks() -> None:
    html = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
  <p>Prose</p>
  <div class="code">fn main() {}</div>
  <p class="code">let x = 1;</p>
  <p>More</p>
</body>
</html>
"""
    root = parse_xhtml_from_string(html)
    assert [u.source_text for u in collect_blocks(root)] == ["Prose", "More"]


def parse_xhtml_from_string(html: str) -> etree._Element:
    parser = etree.XMLParser(recover=True, resolve_entities=False)
    return etree.fromstring(html.encode("utf-8"), parser=parser)
