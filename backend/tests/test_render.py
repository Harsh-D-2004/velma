from app.services.page.render import _html, _inline


def test_inline_markdown_and_br():
    assert _inline("**Model** · RL<br>Date: `x`") == "<strong>Model</strong> · RL<br>Date: <code>x</code>"


def test_inline_still_escapes_html():
    assert _inline("<script>alert(1)</script> <b>x</b>") == "&lt;script&gt;alert(1)&lt;/script&gt; &lt;b&gt;x&lt;/b&gt;"


def test_table_cell_and_code_block():
    out = str(_html("| **A**<br>b |\n|---|\n| c |\n\n```\n**raw**<br>\n```"))
    assert "<th><strong>A</strong><br>b</th>" in out
    assert "<pre><code>**raw**&lt;br&gt;</code></pre>" in out  # code blocks stay literal


def _ws():
    import hashlib
    rw1 = "The model read the config. It found **secrets** in it.\n\nJWTs were parsed without checking signatures."
    rw2 = "Short section about a JWT."
    rev = lambda t: hashlib.sha1(t.encode()).hexdigest()[:8]
    return {
        "title": "Forged tokens", "tldr": "A model **forged** tokens.", "takeaways": ["Check signatures."],
        "section_order": ["s1", "s2"],
        "sections": {"s1": {"title": "Config", "rewritten": rw1, "check": {"passed": True, "rev": rev(rw1)}},
                     "s2": {"title": "Tokens", "rewritten": rw2, "check": {"passed": True, "rev": rev(rw2)}}},
        "diagrams": {"d1": {"section": "s1", "kind": "svg", "url": "https://cdn.example/d1.svg",
                            "alt": "Diagram: How the token was forged", "width": 300, "height": 400}},
        "glossary": {"jwt": {"term": "JWT", "definition": "A signed login token.", "section": "s1",
                             "source_url": "https://example.org/jwt"},
                     "rbac": {"term": "RBAC", "definition": "Role-based access.", "section": "s2", "source_url": None}},
    }


def test_page_layout_pieces():
    from app.services.page.render import build_page_data, check_page, render_page
    ws = _ws()
    data = build_page_data(ws)
    page = render_page(data)
    assert check_page(page, ws) == []
    s1 = str(data["sections"][0]["body_html"])
    assert s1.startswith('<p><span class="lead">The model read the config.</span>')
    assert '</p><aside class="sidenote"><span class="sn"><strong>JWT</strong>' in s1  # before the paragraph that uses it
    assert '<p><span class="term">JWTs</span> were parsed' in s1  # plural matches
    assert data["sections"][1]["notes"][0]["term"] == "RBAC"  # term not in text: stays in the after-section block
    assert 'class="figure float"' in page and "Figure 1</span>How the token was forged" in page
    assert data["minutes"] == 1 and data["figures"] == 1
    assert '<a href="#s2">Tokens</a>' in page and "<dt>JWT</dt>" in page


def test_long_code_is_folded():
    out = str(_html("```py\n" + "\n".join(f"x = {i}" for i in range(12)) + "\n```"))
    assert out.startswith('<details class="code"><summary>Show 12 lines of py</summary><pre>')
