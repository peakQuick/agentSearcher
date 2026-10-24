from agentsearcher.chunk import chunk_markdown


def test_heading_path_and_size():
    md = "# Guide\n\nintro " * 1 + "\n\n## Install\n\n" + ("pip install thing. " * 60)
    chunks = chunk_markdown(md, source=7, size=300)
    assert all(len(c.text) <= 300 for c in chunks)
    assert all(c.source == 7 for c in chunks)
    assert chunks[-1].heading == "Guide > Install"


def test_sibling_heading_replaces_previous():
    md = "# A\n\n## B\n\n" + "b text here. " * 5 + "\n\n## C\n\n" + "c text here. " * 5
    headings = [c.heading for c in chunk_markdown(md, 0, 600)]
    assert headings == ["A > B", "A > C"]


def test_code_block_kept_verbatim():
    code = "```\ndef f():\n\n    return 1\n```"
    md = "Some explanation of the function below.\n\n" + code + "\n\nAfter the code."
    texts = [c.text for c in chunk_markdown(md, 0, 600)]
    assert any("def f():\n\n    return 1" in t for t in texts)


def test_long_code_block_is_refenced():
    code = "```\n" + "\n".join(f"line_{i} = {i} * 1000" for i in range(80)) + "\n```"
    chunks = chunk_markdown(code, 0, 300)
    assert len(chunks) > 1
    assert all(c.text.startswith("```") and c.text.endswith("```") for c in chunks)


def test_tiny_fragments_dropped_but_never_empty():
    assert [c.text for c in chunk_markdown("short", 0, 600)] == ["short"]
    assert chunk_markdown("", 0, 600) == []
