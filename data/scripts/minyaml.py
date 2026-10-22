"""Dependency-free reader for the small YAML subset this repo uses:
a top-level list of flat mappings, with plain scalars, quoted scalars and
folded (`>-`) block scalars. Uses PyYAML when it is installed."""
from __future__ import annotations


def _coerce(v: str):
    s = v.strip()
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if s in ("null", "~", ""):
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _fallback(text: str):
    items, cur, fold_key, fold_lines, fold_indent = [], None, None, [], None
    for raw in text.splitlines():
        if fold_key is not None:
            stripped = raw.strip()
            indent = len(raw) - len(raw.lstrip())
            if stripped and (fold_indent is None or indent >= fold_indent):
                if fold_indent is None:
                    fold_indent = indent
                fold_lines.append(stripped)
                continue
            cur[fold_key] = " ".join(fold_lines)
            fold_key, fold_lines, fold_indent = None, [], None
        line = raw.split(" #")[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if cur is not None:
                items.append(cur)
            cur, stripped = {}, stripped[2:].strip()
        if cur is None or ":" not in stripped:
            continue
        k, _, v = stripped.partition(":")
        k = k.strip()
        if v.strip() in (">-", ">", "|", "|-"):
            fold_key, fold_lines, fold_indent = k, [], None
        else:
            cur[k] = _coerce(v)
    if fold_key is not None:
        cur[fold_key] = " ".join(fold_lines)
    if cur is not None:
        items.append(cur)
    return items


def load_list(path: str):
    text = open(path, encoding="utf-8").read()
    try:
        import yaml
        data = yaml.safe_load(text)
        return data if isinstance(data, list) else []
    except ImportError:
        return _fallback(text)
