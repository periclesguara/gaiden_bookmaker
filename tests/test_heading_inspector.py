from gaiden.tools.heading_inspector import inspect_markdown


def test_heading_inspector_detects_simple_headings():
    headings, chapters = inspect_markdown("# Title\n\n## Chapter 1\n\n## Chapter 2\n")

    assert headings["total_headings"] == 3
    assert headings["h1"] == 1
    assert headings["h2"] == 2
    assert chapters["count"] == 2


def test_heading_inspector_detects_contaminated_heading():
    headings, _chapters = inspect_markdown(
        '## Les Stoïciens<sup><a href="x">88</a></sup> — Raison\n'
    )

    suspicious = headings["suspicious_headings"]
    assert len(suspicious) == 1
    assert "html_sup" in suspicious[0]["reasons"]
    assert "html_link" in suspicious[0]["reasons"]
