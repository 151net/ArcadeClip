"""Extract literal tr()/trn() calls into a gettext template using only the stdlib."""
import ast
import json
from pathlib import Path


def extract(root):
    messages = {}
    for path in sorted((root / "src").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id not in ("tr", "trn"):
                continue
            count = 2 if node.func.id == "trn" else 1
            if any(not isinstance(arg, ast.Constant) or not isinstance(arg.value, str) for arg in node.args[:count]):
                raise ValueError(f"Translation message must be literal: {path}:{node.lineno}")
            key = tuple(arg.value for arg in node.args[:count])
            messages.setdefault(key, []).append(f"{path.relative_to(root).as_posix()}:{node.lineno}")
    return messages


def main():
    root = Path(__file__).resolve().parents[1]
    messages = extract(root)
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    lines = ['msgid ""', 'msgstr ""', quote("Content-Type: text/plain; charset=UTF-8\n"),
             quote("Content-Transfer-Encoding: 8bit\n"), ""]
    for texts, locations in sorted(messages.items()):
        lines.append("#: " + " ".join(locations))
        if "{" in texts[0]:
            lines.append("#, python-brace-format")
        lines.append("msgid " + quote(texts[0]))
        if len(texts) == 2:
            lines.extend(["msgid_plural " + quote(texts[1]), 'msgstr[0] ""', 'msgstr[1] ""'])
        else:
            lines.append('msgstr ""')
        lines.append("")
    target = root / "src/locales/arcadeclip.pot"
    target.parent.mkdir(exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"Extracted {len(messages)} messages: {target}")


if __name__ == "__main__":
    main()
