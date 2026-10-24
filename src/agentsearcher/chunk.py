import re
from dataclasses import dataclass

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
MIN_CHUNK = 40


@dataclass
class Chunk:
    text: str
    heading: str
    source: int


def _split_long(paragraph: str, size: int) -> list[str]:
    if len(paragraph) <= size:
        return [paragraph]
    if paragraph.startswith("```"):
        return _split_code(paragraph, size)
    parts, current = [], ""
    for sentence in SENTENCE_END.split(paragraph):
        while len(sentence) > size:
            parts.append(sentence[:size])
            sentence = sentence[size:]
        if current and len(current) + len(sentence) + 1 > size:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


def _split_code(block: str, size: int) -> list[str]:
    """Split a long fenced block on line boundaries, re-fencing every part."""
    lines = block.strip("`\n").splitlines()
    parts, current = [], []
    for line in lines:
        if current and sum(map(len, current)) + len(line) > size:
            parts.append(current)
            current = []
        current.append(line[:size])
    if current:
        parts.append(current)
    return ["```\n" + "\n".join(p) + "\n```" for p in parts]


def _paragraphs(markdown: str):
    """Yield (heading path, paragraph) pairs, tracking the markdown heading hierarchy.

    Fenced code blocks are kept verbatim as a single paragraph.
    """
    path: list[tuple[int, str]] = []
    buffer: list[str] = []
    in_code = False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            if not in_code and buffer:
                yield " > ".join(t for _, t in path), "\n".join(buffer)
                buffer = []
            in_code = not in_code
            buffer.append(line.strip())
            if not in_code:
                yield " > ".join(t for _, t in path), "\n".join(buffer)
                buffer = []
            continue
        if in_code:
            buffer.append(line.rstrip())
            continue
        match = HEADING.match(line)
        if match or not line.strip():
            if buffer:
                yield " > ".join(t for _, t in path), "\n".join(buffer)
                buffer = []
            if match:
                level = len(match.group(1))
                path = [(lvl, t) for lvl, t in path if lvl < level] + [(level, match.group(2))]
            continue
        buffer.append(line.strip())
    if buffer:
        yield " > ".join(t for _, t in path), "\n".join(buffer)


def chunk_markdown(markdown: str, source: int, size: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    current, current_heading = "", ""
    for heading, paragraph in _paragraphs(markdown):
        for piece in _split_long(paragraph, size):
            if current and (heading != current_heading or len(current) + len(piece) + 1 > size):
                chunks.append(Chunk(current, current_heading, source))
                current = ""
            current = f"{current}\n{piece}" if current else piece
            current_heading = heading
    if current:
        chunks.append(Chunk(current, current_heading, source))
    return [c for c in chunks if len(c.text) >= MIN_CHUNK] or chunks[:1]
