"""Compile this project's UTF-8 gettext catalogs without build dependencies."""
import ast
from pathlib import Path
import struct


def compile_catalog(path):
    messages = {}
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        fields, current = {}, None
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("#, ") and "fuzzy" in line:
                raise ValueError(f"Unreviewed translation in {path}")
            if not line or line.startswith("#"):
                continue
            if not line.startswith('"'):
                current, line = line.split(maxsplit=1)
                if current not in ("msgid", "msgid_plural", "msgstr", "msgstr[0]", "msgstr[1]"):
                    raise ValueError(f"Unsupported PO field: {current}")
                if current in fields:
                    raise ValueError(f"Duplicate PO field: {current}")
                fields[current] = ""
            fields[current] += ast.literal_eval(line)
        if not fields:
            continue
        key = fields["msgid"]
        if "msgid_plural" in fields:
            key += "\0" + fields["msgid_plural"]
            values = [fields[f"msgstr[{i}]"] for i in range(2)]
        else:
            values = [fields["msgstr"]]
        if not all(values) or key in messages:
            raise ValueError(f"Empty or duplicate translation: {key!r}")
        messages[key] = "\0".join(values)

    pairs = sorted((key.encode("utf-8"), value.encode("utf-8")) for key, value in messages.items())
    original = b"".join(key + b"\0" for key, _ in pairs)
    translated = b"".join(value + b"\0" for _, value in pairs)
    count = len(pairs)
    header = struct.pack("<7I", 0x950412DE, 0, count, 28, 28 + count * 8, 0, 0)
    tables = bytearray()
    for column, offset in ((0, 28 + count * 16), (1, 28 + count * 16 + len(original))):
        for pair in pairs:
            tables.extend(struct.pack("<2I", len(pair[column]), offset))
            offset += len(pair[column]) + 1
    return header + tables + original + translated


if __name__ == "__main__":
    for path in sorted((Path(__file__).resolve().parents[1] / "src/locales").glob("*/LC_MESSAGES/*.po")):
        path.with_suffix(".mo").write_bytes(compile_catalog(path))
        print(f"Compiled {path}")
