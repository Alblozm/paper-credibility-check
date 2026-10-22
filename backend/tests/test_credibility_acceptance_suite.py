"""The live acceptance client is itself verified with an offline API transport."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import httpx
import pytest

from scripts.verify_credibility_suite import _canonical, load_samples, run_suite, verify_provenance


def manifest(count=2, pdf=False):
    content = b"%PDF-1.4\nsource pinned test\n"
    samples = [{"sample_id": f"sample_{index}", "title": f"Candidate {index}",
                "input": {"type": "pdf_url" if pdf else "arxiv", "arxiv_id": f"2401.1234{index}v1",
                          "pdf_url": f"https://papers.example.org/{index}.pdf",
                          "pdf_sha256": hashlib.sha256(content).hexdigest()}, "provenance": []}
               for index in range(count)]
    value = {"schema_version": "credibility-acceptance-samples-1", "is_human_gold": False,
             "samples": samples, "coverage_gaps": [{"category": "human_gold", "reason": "pending"}]}
    value["selection_sha256"] = hashlib.sha256(_canonical(value)).hexdigest()
    return value, content


def completed(identifier, sample):
    return {"assessment_id": identifier, "status": "completed", "stage": "done", "progress_percent": 100,
            "data_origin": "live", "paper": {"source_sha256": sample["input"]["pdf_sha256"]}, "error": None,
            "report": {"dimensions": [{"dimension_id": name, "score": score, "applicability": "applicable",
                                       "check_status": "completed"}
                                      for name, score in (("claim_evidence", 3), ("reference_verification", 6),
                                                          ("code_reproduction", 7))],
                       "overall": {"score": 16 / 3}, "coverage": [{"dimension_id": "claim_evidence"}],
                       "execution": {"mode": "parallel", "checks": {
                           name: {"status": "completed", "reused": False}
                           for name in ("claim_check", "reference_check", "code_check")}},
                       "external_review": {"mode": "native_tools", "status": "completed"},
                       "versions": {"model_calls": [{"provider": "deepseek", "purpose": "claim_extract"}]},
                       "sources": [{"snapshot_ref": "snapshot:v1:" + "a" * 64, "fetch_status": "ok"}]}}


def api(manifest_value, pdf_content, *, fail_first=False):
    calls, submitted, poll_counts = [], [], {}

    def handler(request):
        path = request.url.path
        calls.append((request.method, str(request.url)))
        if path == "/health":
            return httpx.Response(200, json={"llm_provider": "deepseek", "parser_provider": "generic"})
        if path == "/v1/models":
            return httpx.Response(200, json=[{"model_id": "model_standard_v1", "provider": "deepseek",
                                             "is_available": True}])
        if request.url.host == "papers.example.org":
            return httpx.Response(200, content=pdf_content)
        if path == "/v1/uploads":
            return httpx.Response(201, json={"upload_id": "upl_" + "b" * 32,
                                             "sha256": hashlib.sha256(pdf_content).hexdigest()})
        if path == "/v1/credibility/assessments" and request.method == "POST":
            submitted.append(json.loads(request.content))
            identifier = f"cred_new_{len(submitted)}"
            return httpx.Response(202, json={"assessment_id": identifier, "status": "queued"})
        if path.startswith("/v1/credibility/assessments/"):
            identifier = path.rsplit("/", 1)[-1]
            index = int(identifier.rsplit("_", 1)[-1]) - 1
            poll_counts[identifier] = poll_counts.get(identifier, 0) + 1
            if poll_counts[identifier] == 1:
                return httpx.Response(200, json={"assessment_id": identifier, "status": "processing",
                                                 "stage": "checking", "progress_percent": 60})
            if fail_first and index == 0:
                return httpx.Response(200, json={"assessment_id": identifier, "status": "failed",
                                                 "error": {"code": "MODEL_ERROR"}})
            return httpx.Response(200, json=completed(identifier, manifest_value["samples"][index]))
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    return httpx.Client(base_url="http://api.example.test", transport=httpx.MockTransport(handler)), calls, submitted


def test_live_client_submits_new_jobs_in_sequence_and_preserves_full_results(tmp_path):
    value, pdf = manifest()
    client, calls, submitted = api(value, pdf)
    result = run_suite(client, value, output=tmp_path, sleep=lambda _: None, poll_seconds=0.01)
    assert result["technical_pass_count"] == result["selected_count"] == 2
    assert len(submitted) == 2 and all(item["rubric_version"] == "0.2-provisional-complete" for item in submitted)
    assert [item["assessment_id"] for item in result["samples"]] == ["cred_new_1", "cred_new_2"]
    assert calls.index(("GET", "http://api.example.test/v1/credibility/assessments/cred_new_1")) < (
        max(i for i, item in enumerate(calls) if item == ("POST", "http://api.example.test/v1/credibility/assessments"))
    )
    assert not any("resume" in url or "replay" in url for _, url in calls)
    saved = json.loads((tmp_path / "sample_0/report.json").read_text())
    assert saved["external_review"]["status"] == "completed" and saved["execution"]["mode"] == "parallel"
    assert json.loads((tmp_path / "frozen-selection.json").read_text()) == value
    assert result["human_gold_validated"] is False


def test_output_cannot_overwrite_or_reuse_a_previous_run(tmp_path):
    value, pdf = manifest(1)
    client, calls, _ = api(value, pdf)
    run_suite(client, value, output=tmp_path, sleep=lambda _: None)
    count = len(calls)
    with pytest.raises(FileExistsError):
        run_suite(client, value, output=tmp_path, sleep=lambda _: None)
    assert len(calls) == count


def test_url_input_must_match_pinned_pdf_before_upload_and_model_submission(tmp_path):
    value, pdf = manifest(1, pdf=True)
    client, calls, submitted = api(value, pdf)
    result = run_suite(client, value, output=tmp_path, sleep=lambda _: None)
    assert result["technical_pass_count"] == 1
    assert (tmp_path / "sample_0/input.pdf").read_bytes() == pdf
    assert submitted[0]["source"]["type"] == "pdf"
    assert calls.index(("GET", "https://papers.example.org/0.pdf")) < calls.index(("POST", "http://api.example.test/v1/uploads"))


def test_modified_pdf_does_not_reach_upload_or_model(tmp_path):
    value, _ = manifest(1, pdf=True)
    client, calls, submitted = api(value, b"%PDF-1.4\nchanged version\n")
    result = run_suite(client, value, output=tmp_path, sleep=lambda _: None)
    assert result["technical_pass_count"] == 0 and not submitted
    assert not any(path.endswith("/uploads") for _, path in calls)
    assert "digest" in result["samples"][0]["issues"][0]


def test_failed_job_is_preserved_and_does_not_prevent_next_fresh_sample(tmp_path):
    value, pdf = manifest()
    client, _, submitted = api(value, pdf, fail_first=True)
    result = run_suite(client, value, output=tmp_path, sleep=lambda _: None)
    assert result["technical_pass_count"] == 1 and len(submitted) == 2
    assert result["samples"][0]["error"] == {"code": "MODEL_ERROR"}
    assert json.loads((tmp_path / "sample_0/assessment.json").read_text())["status"] == "failed"


def test_repeated_job_ids_and_non_live_output_fail_technical_acceptance():
    from scripts.verify_credibility_suite import _summary

    value, _ = manifest(1)
    assessment = completed("old_result", value["samples"][0])
    assessment["data_origin"] = "synthetic"
    assessment["report"]["coverage"] = []
    result = _summary(value["samples"][0], assessment, 1, {"old_result"})
    assert result["technical_acceptance_passed"] is False
    assert any("fresh-job" in message for message in result["issues"])
    assert any("not labelled live" in message for message in result["issues"])
    assert any("coverage" in message for message in result["issues"])


def test_candidate_manifest_is_frozen_before_live_runs_and_not_human_gold(tmp_path):
    value, _ = manifest(1)
    path = tmp_path / "samples.json"
    path.write_text(json.dumps(value))
    assert load_samples(path) == value
    modified = copy.deepcopy(value)
    modified["samples"][0]["manual_expectations"] = ["Adjusted after observing model output"]
    path.write_text(json.dumps(modified))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_samples(path)


def test_repository_selection_has_six_source_pinned_candidates_and_explicit_gaps():
    backend = Path(__file__).resolve().parents[1]
    path = backend / "docs/credibility/acceptance-samples.json"
    value = load_samples(path)
    assert len(value["samples"]) == 6 and value["is_human_gold"] is False
    assert all(sample["selection_status"] == "ai_assisted_pending_human_review" for sample in value["samples"])
    assert any(item["category"] == "non_github_code_host" for item in value["coverage_gaps"])
    provenance = [item for sample in value["samples"] for item in sample["provenance"]]
    assert {item["repository"] for item in provenance} == {"backend", "data", "workspace"}
    for item in provenance:
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        assert not Path(item["path"]).is_absolute() and ".." not in Path(item["path"]).parts
    # The canonical manifest still covers every external digest. Only files
    # shipped by this repository are mandatory in a clean backend checkout.
    builtin = [item for item in provenance if item["repository"] == "backend"]
    assert builtin
    verify_provenance({"samples": [{"provenance": builtin}]}, backend_root=backend)


def test_repository_selection_external_provenance_integration():
    backend = Path(__file__).resolve().parents[1]
    value = load_samples(backend / "docs/credibility/acceptance-samples.json")
    roots = {"data": backend.parent / "data", "workspace": backend.parents[1]}
    missing = [item for sample in value["samples"] for item in sample["provenance"]
               if item["repository"] in roots and not (roots[item["repository"]] / item["path"]).is_file()]
    if missing:
        pytest.skip(f"{len(missing)} external provenance assets unavailable; full integration verification "
                    "requires the sibling data repository and original workspace output files.")
    # Existing but changed assets must fail, never turn a digest mismatch into a skip.
    verify_provenance(value, backend_root=backend)


@pytest.fixture
def provenance_tree(tmp_path):
    workspace = tmp_path / "workspace"
    backend = workspace / "repositories/backend"
    roots = {"backend": backend, "data": backend.parent / "data", "workspace": workspace}
    value, _ = manifest(1)
    for repository, root in roots.items():
        path = root / "evidence/source.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f"Frozen {repository} source.\n".encode()
        path.write_bytes(content)
        value["samples"][0]["provenance"].append({
            "repository": repository, "path": "evidence/source.txt", "sha256": hashlib.sha256(content).hexdigest(),
        })
    value["selection_sha256"] = hashlib.sha256(_canonical({
        key: item for key, item in value.items() if key != "selection_sha256"
    })).hexdigest()
    return backend, roots, value


def test_provenance_verification_checks_all_three_roots_without_external_workspace(provenance_tree):
    backend, _, value = provenance_tree
    verify_provenance(value, backend_root=backend)


@pytest.mark.parametrize("repository", ["backend", "data", "workspace"])
@pytest.mark.parametrize("failure", ["missing", "digest_mismatch", "path_escape"])
def test_strict_provenance_rejects_missing_changed_or_escaped_source(provenance_tree, repository, failure):
    backend, roots, value = provenance_tree
    evidence = next(item for item in value["samples"][0]["provenance"] if item["repository"] == repository)
    path = roots[repository] / evidence["path"]
    if failure == "missing":
        path.unlink()
        with pytest.raises(FileNotFoundError):
            verify_provenance(value, backend_root=backend)
    elif failure == "digest_mismatch":
        path.write_text("Changed after source freeze.")
        with pytest.raises(ValueError, match="Frozen provenance changed"):
            verify_provenance(value, backend_root=backend)
    else:
        # The escaped file has the correct digest; containment itself must reject it.
        escaped = roots[repository].parent / "outside.txt"
        escaped.write_bytes(path.read_bytes())
        evidence["path"] = "../outside.txt"
        with pytest.raises(ValueError, match="path escapes its repository"):
            verify_provenance(value, backend_root=backend)


def test_metadata_requests_match_the_registered_application_routes(tmp_path):
    from app.main import app

    value, pdf = manifest(1)
    client, calls, _ = api(value, pdf)
    run_suite(client, value, output=tmp_path, sleep=lambda _: None)
    registered = app.openapi()["paths"]
    metadata_paths = [httpx.URL(url).path for method, url in calls
                      if method == "GET" and httpx.URL(url).path in {"/health", "/models", "/v1/models"}]
    assert metadata_paths == ["/health", "/v1/models"]
    assert all(path in registered and "get" in registered[path] for path in metadata_paths)


@pytest.mark.parametrize("status", ["failed", "unsupported", "partial", "running", "disabled"])
def test_failed_external_review_cannot_pass_technical_acceptance_despite_numeric_scores(status):
    from scripts.verify_credibility_suite import _summary

    value, _ = manifest(1)
    assessment = completed("new_job", value["samples"][0])
    assessment["report"]["external_review"] = {"mode": "native_tools", "status": status, "error_code": "ValueError"}
    result = _summary(value["samples"][0], assessment, 1, set())
    assert result["pipeline_scoring_passed"] is True
    assert result["external_semantic_acceptance_passed"] is False
    assert result["technical_acceptance_passed"] is False
    assert any(status in message for message in result["external_semantic_issues"])


@pytest.mark.parametrize("status", ["completed", "not_needed"])
def test_enabled_completed_or_not_needed_external_review_passes_semantic_acceptance(status):
    from scripts.verify_credibility_suite import _summary

    value, _ = manifest(1)
    assessment = completed("new_job", value["samples"][0])
    assessment["report"]["external_review"] = {"mode": "prefetch", "status": status}
    result = _summary(value["samples"][0], assessment, 1, set())
    assert result["pipeline_scoring_passed"] is True
    assert result["external_semantic_acceptance_passed"] is True
    assert result["technical_acceptance_passed"] is True


def test_disabled_review_cannot_claim_semantic_pass_using_a_not_needed_label():
    from scripts.verify_credibility_suite import _summary

    value, _ = manifest(1)
    assessment = completed("new_job", value["samples"][0])
    assessment["report"]["external_review"] = {"mode": "native_tools", "enabled": False, "status": "not_needed"}
    result = _summary(value["samples"][0], assessment, 1, set())
    assert result["pipeline_scoring_passed"] is True
    assert result["external_semantic_acceptance_passed"] is False
    assert result["technical_acceptance_passed"] is False


def test_semantic_success_does_not_hide_missing_dimension_scores():
    from scripts.verify_credibility_suite import _summary

    value, _ = manifest(1)
    assessment = completed("new_job", value["samples"][0])
    assessment["report"]["dimensions"][0]["score"] = None
    result = _summary(value["samples"][0], assessment, 1, set())
    assert result["pipeline_scoring_passed"] is False
    assert result["external_semantic_acceptance_passed"] is True
    assert result["technical_acceptance_passed"] is False


@pytest.mark.parametrize("stage", ["claim_check", "reference_check", "code_check"])
@pytest.mark.parametrize("status", ["failed", "partial", "running", "not_run", None])
def test_trial_scores_and_model_attempts_do_not_hide_incomplete_check_execution(stage, status):
    from scripts.verify_credibility_suite import _summary

    value, _ = manifest(1)
    assessment = completed("new_job", value["samples"][0])
    if status is None:
        del assessment["report"]["execution"]["checks"][stage]
    else:
        assessment["report"]["execution"]["checks"][stage]["status"] = status
    result = _summary(value["samples"][0], assessment, 1, set())
    assert result["pipeline_scoring_passed"] is True
    assert result["external_semantic_acceptance_passed"] is True
    assert result["execution_acceptance_passed"] is False
    assert result["technical_acceptance_passed"] is False
    assert any(stage in message for message in result["execution_issues"])


def test_failed_dimension_is_rejected_even_when_old_execution_metadata_claims_completed():
    from scripts.verify_credibility_suite import _summary

    value, _ = manifest(1)
    assessment = completed("old_buggy_execution", value["samples"][0])
    assessment["report"]["dimensions"][0]["check_status"] = "failed"
    result = _summary(value["samples"][0], assessment, 1, set())
    assert result["pipeline_scoring_passed"] is True
    assert result["execution_acceptance_passed"] is False
    assert result["technical_acceptance_passed"] is False
    assert any("claim_evidence=failed" in message for message in result["execution_issues"])


def test_bounded_search_scope_is_separate_from_successful_execution():
    from scripts.verify_credibility_suite import _summary

    value, _ = manifest(1)
    assessment = completed("new_job", value["samples"][0])
    assessment["report"]["dimensions"][2]["check_status"] = "partial"
    result = _summary(value["samples"][0], assessment, 1, set())
    assert result["execution_acceptance_passed"] is True
    assert result["technical_acceptance_passed"] is True
