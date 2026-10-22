"""Pure discovery helpers for source-bound code repository inspection."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit


def repository_scope(url: str) -> dict:
    """Preserve cited GitHub refs/paths without guessing slash-containing refs."""
    parsed = urlsplit(url)
    parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
    if any(
        part in {".", ".."} or "/" in part or "\\" in part or "%" in part or any(ord(char) < 32 for char in part)
        for part in parts
    ):
        return {"scope_invalid": True, "original_url": url}
    if (len(parts) == 3 and parts[2] in {"tree", "blob", "commit"}) or (
        len(parts) == 4 and parts[2:4] == ["releases", "tag"]
    ):
        return {"scope_invalid": True, "original_url": url}
    tail = []
    target_file = None
    if len(parts) > 3 and parts[2] in {"tree", "blob"}:
        tail = parts[3:]
        if parts[2] == "blob":
            if len(tail) < 2:
                return {"scope_invalid": True, "original_url": url}
            target_file = tail[-1]
    elif len(parts) > 3 and parts[2] == "commit":
        tail = [parts[3]]
    elif len(parts) > 4 and parts[2:4] == ["releases", "tag"]:
        tail = parts[4:]
    return {
        "original_url": url,
        "requested_ref_path": tail,
        "requested_revision": tail[0] if tail else None,
        "inspected_subdir": "/".join(tail[1:]) or None,
        "target_file": target_file,
        "ref_scope_resolved": not tail or len(tail) == 1,
        "implementation_checked": False,
        "discussions_checked": False,
        "inspection_status": "pending",
    }


class ProjectHTML(HTMLParser):
    """Read real HTML anchors, preserving their exact source spans for evidence."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.raw = text
        self.offsets = [0]
        self.offsets.extend(match.end() for match in re.finditer("\n", text))
        self.parts, self.anchors = [], []
        self.hidden = 0
        self.anchor = None
        self.heading = None
        self.heading_parts = []
        self.section = ""
        self.feed(text)

    def position(self) -> int:
        line, col = self.getpos()
        return self.offsets[line - 1] + col

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "svg", "noscript"}:
            self.hidden += 1
        if tag in {"h1", "h2", "h3", "h4"}:
            self.heading, self.heading_parts = tag, []
        if tag == "a" and attrs.get("href") and not self.hidden:
            self.anchor = {
                "href": attrs["href"],
                "label": [],
                "start": self.position(),
                "aria": attrs.get("aria-label", ""),
                "section": self.section,
                "context": "".join(self.parts)[-250:],
            }
        if tag in {"br", "p", "div", "h1", "h2", "h3", "h4", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg", "noscript"} and self.hidden:
            self.hidden -= 1
        if tag == self.heading:
            self.section = " ".join(self.heading_parts)
            self.heading = None
        if tag == "a" and self.anchor is not None:
            end = self.raw.find(">", self.position())
            row = self.anchor
            row["raw"] = self.raw[row["start"] : end + 1]
            row["label"] = " ".join(row["label"]).strip() or row["aria"]
            self.anchors.append(row)
            self.anchor = None

    def handle_data(self, text):
        if self.hidden:
            return
        self.parts.append(text)
        if self.heading:
            self.heading_parts.append(text)
        if self.anchor is not None:
            self.anchor["label"].append(text)

    @property
    def text(self):
        return " ".join(" ".join(self.parts).split())


def implementation_paths(rows: list[dict], state: dict) -> list[dict]:
    if state.get("requested_ref_path") and not state.get("ref_scope_resolved", False):
        return []
    subdir = state.get("inspected_subdir") or ""
    target_file = state.get("target_file")
    if target_file and re.fullmatch(r"readme(?:\.(?:md|markdown|mdown|rst|txt))?", target_file, re.I):
        # Preserve target_file in the original scope for ref/path resolution.
        # A README identifies the implementation in its own parent directory;
        # it is not an instruction to inspect only the documentation file.
        target_file = None
    output = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "blob" or row.get("size") == 0:
            continue
        path = str(row.get("path") or "")
        if path.rsplit("/", 1)[-1].casefold() == "setup.py":
            continue  # Packaging metadata remains visible as a dependency manifest.
        if subdir and not path.startswith(subdir + "/"):
            continue
        if target_file and path != (subdir + "/" if subdir else "") + target_file:
            continue
        relative = path[len(subdir) + 1 :] if subdir else path
        if re.search(r"(^|/)(?:docs?|tutorials?|examples?|third[_-]?party|vendor|tests?)(?:/|$)", relative, re.I):
            continue
        if re.search(r"\.(?:py|ipynb|r|jl|cpp|cc|c|m|java|rs|sh)$", path, re.I):
            output.append(row)
    return output


def supported_paper_ids(readme: str) -> list[str]:
    """Read explicit implemented-paper link blocks, never an entire long intro."""
    original = readme
    readme = re.sub(r"```[^\n]*\n.*?```", "", readme, flags=re.S)
    headings = list(re.finditer(r"(?m)^#{2,6}\s+([^\n]+)$", readme))
    intro = readme[: headings[0].start()] if headings else readme
    relation = re.compile(
        r"\b(?:official|our|source)\s+(?:(?:pytorch|tensorflow|jax)\s+)?(?:code|implementation|repository)"
        r"(?:\s+(?:repository|implementation|code))?\s+(?:for|of|implements?|supports?)\b|"
        r"\b(?:we\s+)?(?:provide|release)\s+(?:the\s+)?(?:code|implementations?)\s+(?:for|of)\b",
        re.I,
    )
    identifier = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv\s*:\s*)(\d{4}\.\d{4,5})", re.I)
    irrelevant = re.compile(r"\bbaselines?\b|\bbackbones?\b|related work|references|supported models", re.I)
    aliases = set()

    def collect(value: str) -> None:
        identifiers.update(identifier.findall(value))
        for label, _ in re.findall(r"\[([^]\n]+)\]\((https?://[^)\s]+)\)", value):
            alias = re.sub(r"[^a-z0-9]", "", label.casefold())
            if len(alias) >= 3 and alias not in {"paper", "papers", "arxiv", "code", "project", "website"}:
                aliases.add(alias)

    identifiers = set()
    lines = intro.splitlines()
    for index, line in enumerate(lines):
        plain = re.sub(r"[*_`]", "", line)
        labelled = re.match(r"\s*(?:our\s+)?papers?\s*:\s*(.*)", plain, re.I)
        if labelled and not irrelevant.search(labelled[1]):
            collect(labelled[1])
        if not relation.search(plain) or irrelevant.search(plain):
            continue
        # Only the implementation declaration grants paper aliases. Comparing
        # against, building on, or citing another paper is not code ownership.
        declared = re.split(
            r"\b(?:compared\s+(?:with|to|against)|versus|based\s+on|inspired\s+by|"
            r"we\s+use|depends?\s+on|references|background)\b", plain, maxsplit=1, flags=re.I,
        )[0]
        collect(declared)
        # An official-code declaration authorizes only its adjacent paper-link
        # list, not later paragraphs, result tables or architectural citations.
        following = index + 1
        while following < len(lines) and not lines[following].strip():
            following += 1
        while following < len(lines):
            value = lines[following].strip()
            if not value:
                if following + 1 < len(lines) and re.match(r"\s*[-*+]\s+", lines[following + 1]):
                    following += 1
                    continue
                break
            bullet = re.match(r"(?:[-*+]\s+|\d+[.)]\s+)", value)
            link_only = re.fullmatch(r"(?:\[[^]]+\]\(https?://arxiv\.org/[^)]+\)|https?://arxiv\.org/\S+)", value)
            if not bullet and not link_only:
                break
            if not irrelevant.search(value):
                collect(value)
            following += 1
    for index, heading in enumerate(headings):
        label = " ".join(re.findall(r"[a-z]+", heading[1].casefold()))
        if re.fullmatch(
            r"(?:supported|implemented|official) (?:research )?papers|papers (?:supported|implemented)", label
        ):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(readme)
            for line in readme[heading.end() : end].splitlines():
                if not irrelevant.search(line):
                    collect(line)
    # A supported paper may be introduced through a project page (not an arXiv
    # URL), then linked by the same name later or identified in its BibTeX. Only
    # aliases already authorized above can contribute IDs from these locations.
    for label, url in re.findall(r"\[([^]\n]+)\]\((https?://[^)\s]+)\)", original):
        if re.sub(r"[^a-z0-9]", "", label.casefold()) in aliases:
            identifiers.update(identifier.findall(url))
    for entry in re.split(r"(?i)(?=@(?:article|inproceedings|misc|techreport)\s*\{)", original):
        if not re.match(r"(?i)@(?:article|inproceedings|misc|techreport)\s*\{", entry):
            continue
        depth = 0
        for position in range(entry.index("{"), len(entry)):
            if entry[position] == "{":
                depth += 1
            elif entry[position] == "}":
                depth -= 1
                if depth == 0:
                    entry = entry[: position + 1]
                    break
        else:
            continue
        title = re.search(r"\btitle\s*=\s*[{\"]([^\n]+?)[}\"]\s*,", entry, re.I)
        if title and re.sub(r"[^a-z0-9]", "", title[1].split(":", 1)[0].casefold()) in aliases:
            for field in re.findall(r"\b(?:journal|url|doi)\s*=\s*[{\"]([^\n]+?)[}\"]\s*[,\n]", entry, re.I):
                identifiers.update(identifier.findall(field))
            if re.search(r"\barchiveprefix\s*=\s*[{\"]arxiv[}\"]", entry, re.I):
                identifiers.update(re.findall(r"\beprint\s*=\s*[{\"](\d{4}\.\d{4,5})", entry, re.I))
    return sorted(identifiers)


def readme_entrypoints(readme: str, paths: list[str], subdir: str | None = None) -> list[str]:
    """Read command tokens as data; only report files actually observed in scope."""
    commands = re.findall(
        r"(?:^|[\s`$>])(?:python(?:[23](?:\.\d+)?)?|bash|sh|torchrun)\s+"
        r"(?:(?:-u|-B|-O|-W\s+\S+)\s+)*[\"']?([A-Za-z0-9_./-]+\.(?:py|sh))\b",
        readme,
        re.M,
    )
    commands += [
        value.replace(".", "/") + ".py"
        for value in re.findall(r"\bpython(?:[23])?\s+-m\s+([A-Za-z_][A-Za-z0-9_.]*)", readme)
    ]
    available, output = set(paths), []
    for command in commands:
        while command.startswith("./"):
            command = command[2:]
        if command.startswith("/") or any(part in {"", ".", ".."} for part in command.split("/")):
            continue
        choices = [command, (subdir.rstrip("/") + "/" + command) if subdir else command]
        for path in choices:
            if path in available and path not in output:
                output.append(path)
    return output


def initializer_is_placeholder(text: str) -> bool:
    """Recognize an observed import/metadata-only package shell without running it.

    Syntax we cannot parse is not proof of a placeholder. An initializer may
    itself contain real functions/classes/logic and remains an implementation.
    """
    import ast

    try:
        module = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return False
    metadata = {"__all__", "__version__", "__author__", "__license__", "__copyright__"}
    for node in module.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Pass)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.Assign) and all(
            isinstance(target, ast.Name) and target.id in metadata for target in node.targets
        ):
            try:
                ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError, RecursionError):
                return False
            else:
                continue
        return False
    return True
