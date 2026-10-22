from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.rubrics.loader import RubricLoadError, load_rubric
from app.schemas.enums import DIMENSION_IDS


def test_packaged_rubric_is_valid_and_versioned():
    settings = get_settings()
    rubric = load_rubric(settings.rubric_dir, settings.rubric_id, settings.rubric_version)
    assert rubric.rubric_version == "0.1-draft"
    assert [d.dimension_id for d in rubric.definition.dimensions] == list(DIMENSION_IDS)
    assert len(rubric.content_sha256) == 64
    assert "Soundness & Evidence Support" in rubric.scoring_guide


def test_rubric_rejects_wrong_expected_version():
    settings = get_settings()
    with pytest.raises(RubricLoadError, match="版本不一致"):
        load_rubric(settings.rubric_dir, settings.rubric_id, "9.9")


def test_rubric_rejects_invalid_weights(tmp_path: Path):
    (tmp_path / "rubric.yaml").write_text(
        """rubric_id: test
rubric_version: v1
score_type: integer
score_min: 1
score_max: 10
dimensions:
  - {dimension_id: research_value, weight: 0.1}
  - {dimension_id: soundness_evidence, weight: 0.2}
  - {dimension_id: originality_positioning, weight: 0.2}
  - {dimension_id: clarity_usability, weight: 0.2}
  - {dimension_id: reproducibility_transparency, weight: 0.2}
applicability_values: [applicable, uncertain, not_applicable]
confidence_values: [high, medium, low]
""",
        encoding="utf-8",
    )
    (tmp_path / "scoring_guide.md").write_text("guide", encoding="utf-8")
    with pytest.raises(RubricLoadError, match="weights"):
        load_rubric(str(tmp_path))
