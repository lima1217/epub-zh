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


def test_collect_blocks_skips_section_wrapper_div() -> None:
    """Outer content div wrapping <section> must not become a mega-block."""
    html = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
  <div id="sbo-rt-content">
    <section>
      <h3>Team Trust</h3>
      <p>Trust matters.</p>
    </section>
  </div>
</body>
</html>
"""
    root = parse_xhtml_from_string(html)
    assert [u.source_text for u in collect_blocks(root)] == ["Team Trust", "Trust matters."]


def test_collect_blocks_dedupes_nested_recipe_title() -> None:
    """td wrapping a div label must not double-collect the same title."""
    html = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
  <table class="arr-recipe">
    <tr>
      <td class="arr-recipe-number">
        <div class="arrow-right"><span class="topic-label">Topic 2</span></div>
      </td>
      <td class="arr-recipe-name">The Cat Ate My Source Code</td>
    </tr>
  </table>
  <p>Body paragraph.</p>
</body>
</html>
"""
    root = parse_xhtml_from_string(html)
    texts = [u.source_text for u in collect_blocks(root)]
    assert texts == ["Topic 2", "The Cat Ate My Source Code", "Body paragraph."]
    assert texts.count("Topic 2") == 1


def test_collect_blocks_toc_keeps_chapter_label_not_descendants() -> None:
    """Parent toc li keeps its own title; nested sect items are separate units."""
    html = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
  <nav>
    <ol>
      <li class="toc-chap"><a href="a.xhtml">Foreword</a></li>
      <li class="toc-chap">
        <a href="b.xhtml">Preface to the Second Edition</a>
        <ol>
          <li class="toc-sect"><a href="c.xhtml">How the Book Is Organized</a></li>
          <li class="toc-sect"><a href="d.xhtml">What's in a Name?</a></li>
        </ol>
      </li>
    </ol>
  </nav>
</body>
</html>
"""
    root = parse_xhtml_from_string(html)
    assert [u.source_text for u in collect_blocks(root)] == [
        "Foreword",
        "Preface to the Second Edition",
        "How the Book Is Organized",
        "What's in a Name?",
    ]


def test_collect_blocks_skips_li_that_only_wraps_paragraph() -> None:
    html = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
  <ul>
    <li><p>Only the paragraph should be a unit.</p></li>
  </ul>
</body>
</html>
"""
    root = parse_xhtml_from_string(html)
    assert [u.source_text for u in collect_blocks(root)] == [
        "Only the paragraph should be a unit."
    ]


def parse_xhtml_from_string(html: str) -> etree._Element:
    parser = etree.XMLParser(recover=True, resolve_entities=False)
    return etree.fromstring(html.encode("utf-8"), parser=parser)
