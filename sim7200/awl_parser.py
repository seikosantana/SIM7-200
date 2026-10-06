"""Line-based tokenizer for the S7-200 SMART AWL (STL) subset this project uses.

STL here is one flat instruction per line with no nested expressions or
operator precedence, so a grammar-parser library would be solving a problem
this format doesn't have -- this is a straightforward line tokenizer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_HEADER_RE = re.compile(r"^SUBROUTINE_BLOCK\s+(\w+):SBR(\d+)\s*$")
_NETWORK_RE = re.compile(r"^Network\s+(\d+)\s*$")
_PARAM_RE = re.compile(r"^(\w+)\s*:\s*BOOL\s*;\s*$")


class AwlParseError(ValueError):
    pass


@dataclass
class Instruction:
    op: str
    args: list[str]
    key: str  # stable id ("SUBRNAME#network.index") -- used for EU's per-instance edge memory


@dataclass
class Network:
    number: int
    comment: str
    instructions: list[Instruction] = field(default_factory=list)


@dataclass
class Subroutine:
    name: str
    sbr_num: int
    params: list[str]
    networks: list[Network]
    source_file: str

    def all_instructions(self):
        for net in self.networks:
            yield from net.instructions


def parse_text(text: str, source_file: str) -> Subroutine:
    lines = text.splitlines()
    i = 0

    def next_line() -> str | None:
        nonlocal i
        while i < len(lines):
            line = lines[i]
            i += 1
            if line.strip() != "":
                return line
        return None

    header = next_line()
    if header is None:
        raise AwlParseError(f"{source_file}: empty file")
    m = _HEADER_RE.match(header.strip())
    if not m:
        raise AwlParseError(f"{source_file}: expected SUBROUTINE_BLOCK header, got {header!r}")
    name, sbr_num = m.group(1), int(m.group(2))

    params: list[str] = []
    line = next_line()
    if line is not None and line.strip().startswith("TITLE="):
        line = next_line()
    if line is not None and line.strip() == "VAR_IN_OUT":
        while True:
            line = next_line()
            if line is None:
                raise AwlParseError(f"{source_file}: unterminated VAR_IN_OUT")
            if line.strip() == "END_VAR":
                break
            pm = _PARAM_RE.match(line.strip())
            if not pm:
                raise AwlParseError(f"{source_file}: bad VAR_IN_OUT line {line!r}")
            params.append(pm.group(1))
        line = next_line()

    if line is None or line.strip() != "BEGIN":
        raise AwlParseError(f"{source_file}: expected BEGIN, got {line!r}")

    networks: list[Network] = []
    current: Network | None = None
    pending_comment: list[str] = []

    while True:
        line = next_line()
        if line is None:
            raise AwlParseError(f"{source_file}: unterminated SUBROUTINE_BLOCK")
        stripped = line.strip()
        if stripped == "END_SUBROUTINE_BLOCK":
            break
        nm = _NETWORK_RE.match(stripped)
        if nm:
            current = Network(number=int(nm.group(1)), comment="")
            networks.append(current)
            pending_comment = []
            continue
        if stripped.startswith("//"):
            pending_comment.append(stripped[2:].strip())
            if current is not None and not current.comment:
                current.comment = " ".join(pending_comment)
            continue
        # instruction line
        if current is None:
            raise AwlParseError(f"{source_file}: instruction outside any Network: {line!r}")
        parts = stripped.split(None, 1)
        op = parts[0]
        args = [a.strip() for a in parts[1].split(",")] if len(parts) > 1 else []
        idx = len(current.instructions)
        current.instructions.append(Instruction(op=op, args=args, key=f"{name}#{current.number}.{idx}"))

    return Subroutine(name=name, sbr_num=sbr_num, params=params, networks=networks, source_file=source_file)


def parse_file(path: str | Path) -> Subroutine:
    path = Path(path)
    return parse_text(path.read_text(encoding="utf-8"), str(path))
