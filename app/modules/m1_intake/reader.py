"""按编码回退链读取元数据文件，保留原始行号。

真实导出件的三件事在这里处理：字节序标记、编码回退、CR/LF 混用。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app import config, errors


@dataclass(frozen=True)
class RawFile:
    name: str
    sha256: str
    encoding: str
    bom: bool
    fallback: bool
    size: int
    text: str
    lines: tuple[tuple[int, str], ...]

    @property
    def line_count(self) -> int:
        return len(self.lines)


BOM = b"\xef\xbb\xbf"


def decode_bytes(data: bytes) -> dict:
    """按回退链解码，返回命中的编码与是否回退。

    标记必须在探测编码之前剥掉：带着标记的 GB18030 文件用任何编码都解不出来。
    """
    bom = data.startswith(BOM)
    body = data[len(BOM):] if bom else data
    used = None
    text = None
    tried: list[str] = []
    for encoding in config.ENCODING_CHAIN:
        tried.append(encoding)
        try:
            text = body.decode(encoding)
        except UnicodeDecodeError:
            continue
        used = encoding
        fallback = encoding not in ("utf-8-sig", "utf-8")
        break
    if used is None or text is None:
        raise errors.BadInput(
            "样例编码无法识别",
            detail={"tried": tried, "bytes": len(data)},
        )
    return {"encoding": used, "text": text, "fallback": fallback, "tried": tried,
            "bom": bom}


def _split_lines(text: str) -> tuple[tuple[int, str], ...]:
    out: list[tuple[int, str]] = []
    for index, raw in enumerate(text.split("\n"), start=1):
        line = raw[:-1] if raw.endswith("\r") else raw
        # 分段导出时，标记可能出现在任意一段的首列名上
        if line.startswith("\ufeff"):
            line = line[1:]
        out.append((index, line))
    return tuple(out)


def read_meta_file(path: Path) -> RawFile:
    data = path.read_bytes()
    decoded = decode_bytes(data)
    text = decoded["text"]
    bom = bool(decoded["bom"]) or text.startswith("\ufeff")
    if text.startswith("\ufeff"):
        text = text[1:]
    return RawFile(
        name=path.name,
        sha256=hashlib.sha256(data).hexdigest(),
        encoding=decoded["encoding"],
        bom=bom,
        fallback=bool(decoded["fallback"]),
        size=len(data),
        text=text,
        lines=_split_lines(text),
    )
