"""Regression boundaries from README-blob and explicitly shared-paper runs."""

from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path

import pytest

from app.credibility import code_checks
from app.credibility.code_lookup import Fetch
from app.credibility.code_repository_scope import implementation_paths, repository_scope, supported_paper_ids
from app.credibility.context import CheckContext
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

SHA = "b545c2dcfe42b2184a338606a0df71aaf8535021"
REPO = "brendel-group/objects-compositional-generalization"
API = "https://api.github.com/repos/" + REPO
TITLE = "Provable Compositional Generalization for Object-Centric Learning"


def tree(*paths):
    return [{"path": path, "type": "blob", "size": 100} for path in paths]


def check_blob(monkeypatch, tmp_path, target, *, prefix="", resolve=True, implementation=True):
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    url = f"https://github.com/{REPO}/blob/submission/{prefix}{target}"
    source = f"Our code is available at {url}"
    ctx = CheckContext(
        source={"type": "arxiv", "arxiv_id": "2310.05327v2"}, repo_root=Path("."),
        generated_at="2026-10-19T00:00:00Z", ref=None, unresolved_reason=None,
        doc=ParsedDocument(
            paper=Paper(paper_id="object", title=TITLE, arxiv_id="2310.05327v2", parse_quality=ParseQuality.GOOD),
            blocks=[TextBlock(text=source, chunk_id="paper_code", page_start=1, section_role="introduction")],
        ),
    )
    def content(text):
        return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}

    directory = prefix.rstrip("/")
    scoped_readme = API + "/readme" + ("/" + directory if directory else "") + "?ref=" + SHA
    paths = [prefix + "README.md", "outside/train.py" if prefix else "docs/example.py"]
    if implementation:
        paths += [prefix + "main.py", prefix + "src/train.py"]
    routes = {
        API: {"full_name": REPO, "default_branch": "main"},
        API + "/git/matching-refs/heads/submission": [{"ref": "refs/heads/submission"}] if resolve else [],
        API + "/git/matching-refs/tags/submission": [],
        API + "/commits/submission": {"sha": SHA},
        scoped_readme: content(f"# {TITLE}\nOfficial code for this paper.\npython main.py\n"),
        API + f"/git/trees/{SHA}?recursive=1": {"tree": tree(*paths)},
        API + "/contents/" + prefix + "main.py?ref=" + SHA: content("def train():\n    return 42\n"),
        API + "/issues?state=all&sort=updated&per_page=20&page=1": [],
    }
    calls = []

    def get(url):
        calls.append(url)
        assert url in routes, f"Unexpected request or fallback: {url}"
        return Fetch(url, "ok", 200, "2026-10-19T00:00:00Z", data=routes[url])

    monkeypatch.setattr(code_checks.LOOKUP, "get", get)
    return code_checks.check_code(ctx), calls


@pytest.mark.parametrize("prefix", ["", "submission_files/"])
def test_readme_blob_inspects_implementation_in_same_ref_parent(monkeypatch, tmp_path, prefix):
    result, calls = check_blob(monkeypatch, tmp_path, "README.md", prefix=prefix)
    repository = result.checks.repositories[0]
    state = result.followup_state[repository.repository_id]
    assert result.checks.code_availability == "available"
    assert repository.inspected_revision == SHA and repository.implementation_evidence_ids
    assert state["requested_revision"] == "submission"
    assert state["inspected_subdir"] == (prefix.rstrip("/") or None)
    assert state["target_file"] == "README.md"  # Original blob provenance is preserved.
    assert API + "/contents/" + prefix + "main.py?ref=" + SHA in calls
    assert not any("/commits/main" in url or "/contents/outside/" in url for url in calls)


def test_unresolved_readme_branch_does_not_substitute_default(monkeypatch, tmp_path):
    result, calls = check_blob(monkeypatch, tmp_path, "README.md", resolve=False)
    assert result.checks.code_availability != "available" and result.dimension.check_status == "partial"
    assert not any("/commits/" in url or "/git/trees/" in url for url in calls)


def test_readme_only_directory_does_not_become_available(monkeypatch, tmp_path):
    # Outside code must not rescue an explicitly cited documentation subdirectory.
    result, _ = check_blob(monkeypatch, tmp_path, "README.md", prefix="paper/", implementation=False)
    assert result.checks.code_availability != "available"
    assert not result.checks.repositories[0].implementation_evidence_ids


@pytest.mark.parametrize("document", ["README", "README.md", "readme.rst", "ReadMe.txt", "README.markdown"])
def test_document_target_filter_keeps_parent_scope_and_input_state(document):
    state = {"inspected_subdir": "paper", "target_file": document, "inspected_revision": SHA}
    original = deepcopy(state)
    rows = tree("paper/main.py", "paper/src/layer.py", "paper/docs/sample.py", "other/main.py", "paperish/main.py")
    assert [r["path"] for r in implementation_paths(rows, state)] == ["paper/main.py", "paper/src/layer.py"]
    assert state == original


@pytest.mark.parametrize("target", ["train.py", "readme.py", "README.ipynb"])
def test_explicit_source_blob_stays_a_single_file(target):
    rows = tree("paper/" + target, "paper/other.py", "other/" + target)
    state = {"inspected_subdir": "paper", "target_file": target}
    assert [r["path"] for r in implementation_paths(rows, state)] == ["paper/" + target]


def test_readme_scope_cannot_be_inferred_before_ref_resolution():
    state = repository_scope(f"https://github.com/{REPO}/blob/release/paper/README.md")
    assert implementation_paths(tree("main.py", "release/paper/main.py"), state) == []


CTRL_WORLD_README = """<h2>Ctrl-World: A Controllable Generative World Model for Robot Manipulation</h2>
<a href='https://arxiv.org/abs/2510.10125'><img src='badge'></a>
""" + (
    "This repo includes the official PyTorch implementation for ICLR 2026 "
    "[**Ctrl-World**](https://sites.google.com/view/ctrl-world) paper. "
    "And also include the world model post-training process in "
    "[**VLAW**](https://sites.google.com/view/vlaw-arxiv) paper.\n"
) + """
## Training
You can post-train the world model as in paper [VLAW](https://arxiv.org/abs/2602.12063).
## Acknowledgement
We use a backbone from https://arxiv.org/abs/2112.10752 and policies from https://arxiv.org/abs/2410.24164.
## Bibtex
```
@article{guo2025ctrl,
 title={Ctrl-world: A controllable generative world model for robot manipulation},
 journal={arXiv preprint arXiv:2510.10125},
 year={2025}
}
@article{guo2026vlaw,
 title={Vlaw: Iterative co-improvement of vision-language-action policy and world model},
 journal={arXiv preprint arXiv:2602.12063},
 year={2026}
}
@article{other,
 title={A different cited model},
 journal={arXiv preprint arXiv:2201.11111},
 year={2022}
}
```
"""


def test_current_two_paper_readme_links_only_explicitly_supported_aliases():
    assert supported_paper_ids(CTRL_WORLD_README) == ["2510.10125", "2602.12063"]


def test_generic_project_alias_can_bind_same_named_arxiv_and_bibtex():
    text = """# Method toolbox
Official JAX implementation for [Method-X](https://lab.example/x) and [Method-Y](https://lab.example/y).
## Usage
See [Method-Y](https://arxiv.org/abs/2402.22222).
## Citation
```bibtex
@article{x,
 title={Method-X: Efficient structured learning},
 journal={arXiv preprint arXiv:2401.11111},
 note={Compares against https://arxiv.org/abs/2101.44444}
}
```
Unrelated architectural citation https://arxiv.org/abs/2001.33333.
"""
    assert supported_paper_ids(text) == ["2401.11111", "2402.22222"]


def test_own_paper_label_does_not_authorize_lora_style_long_intro_references():
    text = """# Efficient fine-tuning
Paper: https://arxiv.org/abs/2106.09685
We evaluate on RoBERTa https://arxiv.org/abs/1907.11692 and DeBERTa https://arxiv.org/abs/2006.03654.
| Adapter | https://arxiv.org/abs/1902.00751 |
| Prefix | https://arxiv.org/abs/2101.00190 |
## Quickstart
"""
    assert supported_paper_ids(text) == ["2106.09685"]


def test_comparison_link_and_unrelated_bibtex_do_not_grant_supported_paper():
    text = """Official code repository for [Our-Method](https://lab.example/ours). Compared with [Other-Method](https://arxiv.org/abs/2402.22222).
## Citation
```bibtex
@article{ours,
 title={Our-Method: Learning},
 journal={arXiv preprint arXiv:2401.11111}
}
@article{other,
 title={Other-Method: A baseline},
 journal={arXiv preprint arXiv:2402.22222}
}
```
"""
    assert supported_paper_ids(text) == ["2401.11111"]


def test_non_ownership_project_link_does_not_activate_citation_aliases():
    text = CTRL_WORLD_README.replace(
        "This repo includes the official PyTorch implementation", "We cite this implementation"
    )
    assert supported_paper_ids(text) == []
