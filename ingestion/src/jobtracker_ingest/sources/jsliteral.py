"""Read the JavaScript literals that seroval writes into a page.

Sites built with TanStack Start (kariera.gr since 2026-11-04) send their
state as a script like

    $_TSR.router=($R=>$R[0]={job:$R[1]={id:7,title:"A",remote:!1}})($R["tsr"])

This is JavaScript, not JSON: keys have no quotes, true and false are !0
and !1, undefined is void 0, and $R[n]=value stores a value in slot n so a
later $R[n] can point back at it.

read_value reads one such literal into plain Python values. It knows only
the parts seroval uses for plain data and raises ValueError on anything
else, such as a function or new Map(...), so a caller can skip that value.
"""

import re
from typing import Any

SLOT = re.compile(r"\$R\[(\d+)\]")
KEY = re.compile(r"[A-Za-z_$][\w$]*|\d+")
NUMBER = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
STRING = {
    '"': re.compile(r'"((?:[^"\\]|\\.)*)"', re.S),
    "'": re.compile(r"'((?:[^'\\]|\\.)*)'", re.S),
}
ESCAPE = re.compile(r"\\(?:x([0-9a-fA-F]{2})|u\{([0-9a-fA-F]+)\}|u([0-9a-fA-F]{4})|(.))", re.S)
SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
LINE_BREAKS = "\n\r\u2028\u2029"
WORDS: dict[str, Any] = {
    "!0": True,
    "!1": False,
    "null": None,
    "void 0": None,
    "undefined": None,
    "true": True,
    "false": False,
}


def read_value(text: str, start: int = 0) -> tuple[Any, int]:
    """The literal that starts at text[start], and the index just past it."""
    return Reader(text).value(start)


def unescape(body: str) -> str:
    """The text of a JavaScript string literal, without its quotes."""

    def replace(match: re.Match[str]) -> str:
        hex2, code_point, hex4, char = match.groups()
        if hex2 or hex4:
            return chr(int(hex2 or hex4, 16))
        if code_point:
            return chr(int(code_point, 16))
        # A backslash before a line break continues the string on the next line
        if char in LINE_BREAKS:
            return ""
        return SIMPLE_ESCAPES.get(char, char)

    text = ESCAPE.sub(replace, body)
    # \uD83D\uDE00 style pairs come out as two halves; join them back up
    return text.encode("utf-16", "surrogatepass").decode("utf-16")


class Reader:
    def __init__(self, text: str) -> None:
        self.text = text
        # Values stored with $R[n]=..., so a later $R[n] can be looked up.
        # A slot filled before the part being read stays unknown and reads
        # as None.
        self.slots: dict[str, Any] = {}

    def value(self, pos: int) -> tuple[Any, int]:
        pos = self.skip_space(pos)
        slot = SLOT.match(self.text, pos)
        if slot:
            end = self.skip_space(slot.end())
            if self.text.startswith("=", end) and not self.text.startswith("==", end):
                value, end = self.value(end + 1)
                self.slots[slot.group(1)] = value
                return value, end
            return self.slots.get(slot.group(1)), slot.end()

        char = self.text[pos : pos + 1]
        if char == "{":
            return self.object(pos + 1)
        if char == "[":
            return self.array(pos + 1)
        if char in STRING:
            string = STRING[char].match(self.text, pos)
            if not string:
                raise ValueError(f"unterminated string at {pos}")
            return unescape(string.group(1)), string.end()
        for word, value in WORDS.items():
            if self.text.startswith(word, pos) and not self.continues_word(pos + len(word)):
                return value, pos + len(word)
        number = NUMBER.match(self.text, pos)
        if number and not self.continues_word(number.end()):
            found = number.group()
            return (float(found) if any(c in found for c in ".eE") else int(found)), number.end()
        raise ValueError(f"unsupported JavaScript at {pos}: {self.text[pos : pos + 20]!r}")

    def object(self, pos: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        pos = self.skip_space(pos)
        if self.text.startswith("}", pos):
            return result, pos + 1
        while True:
            key, pos = self.key(pos)
            pos = self.expect(pos, ":")
            result[key], pos = self.value(pos)
            pos = self.skip_space(pos)
            if self.text.startswith("}", pos):
                return result, pos + 1
            pos = self.expect(pos, ",")

    def array(self, pos: int) -> tuple[list[Any], int]:
        result: list[Any] = []
        pos = self.skip_space(pos)
        if self.text.startswith("]", pos):
            return result, pos + 1
        while True:
            pos = self.skip_space(pos)
            # seroval writes [,x] for an array with a hole in it
            if self.text.startswith((",", "]"), pos):
                result.append(None)
            else:
                item, pos = self.value(pos)
                result.append(item)
                pos = self.skip_space(pos)
            if self.text.startswith("]", pos):
                return result, pos + 1
            pos = self.expect(pos, ",")

    def key(self, pos: int) -> tuple[str, int]:
        pos = self.skip_space(pos)
        char = self.text[pos : pos + 1]
        if char in STRING:
            value, end = self.value(pos)
            return str(value), end
        match = KEY.match(self.text, pos)
        if not match:
            raise ValueError(f"expected a key at {pos}")
        return match.group(), match.end()

    def expect(self, pos: int, char: str) -> int:
        pos = self.skip_space(pos)
        if not self.text.startswith(char, pos):
            raise ValueError(f"expected {char!r} at {pos}")
        return pos + 1

    def skip_space(self, pos: int) -> int:
        while pos < len(self.text) and self.text[pos].isspace():
            pos += 1
        return pos

    def continues_word(self, pos: int) -> bool:
        """True when text[pos] would make the word before it longer, as in nullable."""
        return pos < len(self.text) and (self.text[pos].isalnum() or self.text[pos] in "_$")
