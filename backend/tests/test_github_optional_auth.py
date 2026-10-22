"""Optional GitHub credentials are scoped to the validated API origin only."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.credibility.code_lookup import PublicCodeLookup
from app.credibility.snapshots import read_snapshot

_TEST_TOKEN = "github_test_credential_do_not_store"


@pytest.fixture(autouse=True)
def isolated_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))


def lookup_with_transport(monkeypatch, handler, *, token=_TEST_TOKEN, public=True):
    settings = Settings(_env_file=None, github_token=SecretStr(token))
    monkeypatch.setattr("app.credibility.code_lookup.get_settings", lambda: settings)
    original = httpx.Client
    monkeypatch.setattr("app.credibility.code_lookup.httpx.Client",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    lookup = PublicCodeLookup(interval=0)
    monkeypatch.setattr(lookup, "_public", lambda _url: public)
    return lookup, settings


def test_token_is_secret_excluded_from_settings_serialization_and_task_fingerprints(monkeypatch):
    from app.credibility.runtime import request_versions

    settings = Settings(_env_file=None, github_token=SecretStr(_TEST_TOKEN))
    assert settings.github_token.get_secret_value() == _TEST_TOKEN
    assert _TEST_TOKEN not in repr(settings) and "github_token" not in settings.model_dump()
    assert _TEST_TOKEN not in settings.model_dump_json()
    monkeypatch.setattr("app.credibility.runtime.get_settings", lambda: settings)
    before = request_versions()
    settings.github_token = SecretStr("another_credential")
    after = request_versions()
    assert before == after and _TEST_TOKEN not in json.dumps(before)
    assert "another_credential" not in json.dumps(after)


def test_token_environment_is_optional_and_not_exported(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert Settings(_env_file=None).github_token.get_secret_value() == ""
    monkeypatch.setenv("GITHUB_TOKEN", _TEST_TOKEN)
    settings = Settings(_env_file=None)
    assert settings.github_token.get_secret_value() == _TEST_TOKEN
    assert "github_token" not in settings.model_dump(mode="json")


def test_api_authorization_is_sent_but_never_saved_or_logged(monkeypatch, tmp_path, caplog):
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json={"full_name": "public/repository"})

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    with caplog.at_level(logging.INFO):
        result = lookup.get("https://api.github.com/repos/public/repository")
    assert observed[0].headers["authorization"] == "Bearer " + _TEST_TOKEN
    assert result.status == "ok"
    snapshot = read_snapshot(result.snapshot_ref)
    assert snapshot.normalized_json == {"full_name": "public/repository"}
    assert _TEST_TOKEN not in caplog.text and _TEST_TOKEN not in repr(result)
    for path in Path(tmp_path).rglob("*"):
        if path.is_file():
            assert _TEST_TOKEN.encode() not in path.read_bytes()
    assert all("Authorization" not in field for field in snapshot.manifest)


@pytest.mark.parametrize("url", [
    "https://github.com/public/repository",
    "https://raw.githubusercontent.com/public/repository/main/README.md",
    "https://api.github.com.evil.example/repos/public/repository",
    "https://example.org/api.github.com",
    "https://api.github.com:8443/repos/public/repository",
    "http://api.github.com/repos/public/repository",
])
def test_other_origins_or_nonstandard_authorities_never_receive_token(monkeypatch, url):
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(200, text="public source")

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    assert lookup.get(url).status == "ok"  # _public injected here to test the additional credential guard.
    assert all("authorization" not in request.headers for request in observed)


def test_redirect_rebuilds_headers_and_strips_token_from_web_and_raw_origins(monkeypatch):
    observed = []

    def handler(request):
        observed.append(request)
        if request.url.host == "api.github.com" and request.url.path == "/repos/public/repository":
            return httpx.Response(200, json={"full_name": "public/repository", "private": False})
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"location": "https://github.com/public/repository"})
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"location": "https://raw.githubusercontent.com/public/repo/README.md"})
        return httpx.Response(200, text="actual public README")

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    assert lookup.get("https://api.github.com/repos/public/repository").status == "ok"
    answer = lookup.get("https://api.github.com/repos/public/repository/readme")
    assert answer.status == "ok" and len(observed) == 4
    assert observed[1].headers["authorization"] == "Bearer " + _TEST_TOKEN
    assert all("authorization" not in request.headers for request in observed[2:])


def test_blocked_target_is_rejected_before_any_credential_bearing_request(monkeypatch):
    observed = []
    lookup, _ = lookup_with_transport(monkeypatch, lambda request: observed.append(request), public=False)
    result = lookup.get("https://api.github.com/repos/public/repository")
    assert result.status == "access_denied" and observed == []
    assert read_snapshot(result.snapshot_ref).manifest["fetch_status"] == "access_denied"


def test_unconfigured_token_keeps_anonymous_api_requests(monkeypatch):
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json={"ok": True})

    lookup, _ = lookup_with_transport(monkeypatch, handler, token="")
    assert lookup.get("https://api.github.com/repos/public/repository").status == "ok"
    assert "authorization" not in observed[0].headers


@pytest.mark.parametrize("code,headers,status", [
    (401, {}, "access_denied"), (403, {}, "access_denied"),
    (403, {"x-ratelimit-remaining": "0"}, "rate_limited"), (429, {}, "rate_limited"),
    (404, {}, "not_found"),
])
def test_authentication_and_rate_limit_errors_remain_explicit(monkeypatch, code, headers, status):
    lookup, _ = lookup_with_transport(monkeypatch, lambda _request: httpx.Response(code, headers=headers))
    answer = lookup.get("https://api.github.com/repos/public/repository")
    assert answer.status == status and answer.http_status == code
    snapshot = read_snapshot(answer.snapshot_ref)
    assert snapshot.manifest["fetch_status"] == status and snapshot.manifest["http_status"] == code
    assert snapshot.raw is None


def test_credential_url_is_rejected_by_public_validation_before_httpx_can_add_basic_auth(monkeypatch):
    observed = []
    lookup, _ = lookup_with_transport(monkeypatch, lambda request: observed.append(request))
    monkeypatch.setattr(lookup, "_public", PublicCodeLookup._public)
    answer = lookup.get("https://user:password@api.github.com/repos/public/repository")
    assert answer.status == "access_denied" and observed == []


def test_private_repository_metadata_is_denied_without_saving_its_body(monkeypatch, tmp_path):
    private_body = "private repository description must not enter snapshots"
    observed = []

    def handler(request):
        observed.append(request)
        if request.url.path == "/repos/private/repository":
            return httpx.Response(200, json={"private": True, "full_name": "private/repository",
                                             "description": private_body})
        assert "authorization" not in request.headers
        return httpx.Response(404)

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    denied = lookup.get("https://api.github.com/repos/private/repository")
    assert denied.status == "access_denied" and denied.http_status == 200
    assert denied.text == "" and denied.data is None
    snapshot = read_snapshot(denied.snapshot_ref)
    assert snapshot.raw is None and snapshot.normalized_json is None
    assert snapshot.manifest["metadata"]["reason"] == "private_repository_not_supported"
    assert lookup.get("https://api.github.com/repos/private/repository/readme").status == "not_found"
    for path in Path(tmp_path).rglob("*"):
        if path.is_file():
            assert private_body.encode() not in path.read_bytes()


def test_repository_content_is_authenticated_only_after_public_metadata_proof(monkeypatch):
    observed = []

    def handler(request):
        observed.append(request)
        if request.url.path == "/repos/public/repository":
            return httpx.Response(200, json={"private": False, "full_name": "public/repository"})
        return httpx.Response(200, json={"public": "contents"})

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    lookup.get("https://api.github.com/repos/public/repository/readme")
    lookup.get("https://api.github.com/repos/public/repository")
    lookup.get("https://api.github.com/repos/public/repository/contents/model.py")
    lookup.get("https://api.github.com/user")
    assert "authorization" not in observed[0].headers  # No known public repository yet.
    assert observed[1].headers["authorization"] == "Bearer " + _TEST_TOKEN
    assert observed[2].headers["authorization"] == "Bearer " + _TEST_TOKEN
    assert "authorization" not in observed[3].headers


def test_metadata_numeric_canonical_redirect_retains_auth_and_public_aliases(monkeypatch):
    observed = []

    def handler(request):
        observed.append(request)
        if request.url.path == "/repos/old/repository":
            return httpx.Response(301, headers={"location": "https://api.github.com/repositories/123"})
        return httpx.Response(200, json={"id": 123, "private": False, "full_name": "new/repository"})

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    answer = lookup.get("https://api.github.com/repos/old/repository")
    assert answer.status == "ok" and len(observed) == 2
    assert all(request.headers["authorization"] == "Bearer " + _TEST_TOKEN for request in observed)
    assert lookup._public_repositories == {"old/repository", "new/repository"}
    assert read_snapshot(answer.snapshot_ref).manifest["final_url"] == "https://api.github.com/repositories/123"


def test_known_public_repository_same_path_numeric_redirect_retains_auth(monkeypatch):
    observed = []

    def handler(request):
        observed.append(request)
        if request.url.path == "/repos/old/repository":
            return httpx.Response(200, json={"private": False, "full_name": "new/repository"})
        if request.url.path == "/repos/old/repository/readme":
            return httpx.Response(301, headers={"location": "https://api.github.com/repositories/123/readme"})
        return httpx.Response(200, json={"encoding": "base64", "content": "cHVibGlj"})

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    lookup.get("https://api.github.com/repos/old/repository")
    assert lookup.get("https://api.github.com/repos/old/repository/readme").status == "ok"
    assert len(observed) == 3 and all("authorization" in request.headers for request in observed)


@pytest.mark.parametrize("path", ["/repositories/123", "/repositories/123/readme", "/user"])
def test_direct_numeric_or_user_api_never_inherits_canonical_redirect_permission(monkeypatch, path):
    observed = []

    def handler(request):
        observed.append(request)
        if request.url.path == "/repos/public/repository":
            return httpx.Response(200, json={"id": 123, "private": False, "full_name": "public/repository"})
        return httpx.Response(200, json={"ok": True})

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    lookup.get("https://api.github.com/repos/public/repository")
    lookup.get("https://api.github.com" + path)
    assert "authorization" not in observed[-1].headers


@pytest.mark.parametrize("redirect", [
    "https://api.github.com/repositories/123/contents/secret.py",
    "https://api.github.com/user",
    "https://api.github.com.evil.example/repositories/123",
    "https://raw.githubusercontent.com/repositories/123",
])
def test_canonical_redirect_permission_never_changes_operation_or_origin(monkeypatch, redirect):
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(302, headers={"location": redirect}) if len(observed) == 1 else (
            httpx.Response(200, json={"ok": True})
        )

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    assert lookup.get("https://api.github.com/repos/public/repository").status == "ok"
    assert len(observed) == 2 and "authorization" in observed[0].headers
    assert "authorization" not in observed[1].headers


def test_private_canonical_metadata_is_rejected_without_body_or_alias_promotion(monkeypatch, tmp_path):
    marker = "private content not for evidence"

    def handler(request):
        if request.url.path == "/repos/old/repository":
            return httpx.Response(301, headers={"location": "https://api.github.com/repositories/123"})
        assert "authorization" in request.headers
        return httpx.Response(200, json={"private": True, "full_name": "private/repository", "description": marker})

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    answer = lookup.get("https://api.github.com/repos/old/repository")
    assert answer.status == "access_denied" and not lookup._public_repositories
    assert answer.data is None and read_snapshot(answer.snapshot_ref).raw is None
    assert all(marker.encode() not in path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())


def test_anonymous_rate_limit_cannot_block_authenticated_repository_metadata(monkeypatch):
    observed = []

    def handler(request):
        observed.append(request)
        if "authorization" not in request.headers:
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "retry-after": "3600"})
        return httpx.Response(200, json={"private": False, "full_name": "public/repository"})

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    assert lookup.get("https://api.github.com/repositories/123").status == "rate_limited"
    assert lookup.get("https://api.github.com/repos/public/repository").status == "ok"
    assert lookup.get("https://api.github.com/repositories/456").http_status is None
    assert len(observed) == 2
    assert "api.github.com:anonymous" in lookup._cooldown
    assert "api.github.com:authenticated" not in lookup._cooldown


def test_authenticated_rate_limit_does_not_block_anonymous_api_route(monkeypatch):
    observed = []

    def handler(request):
        observed.append(request)
        if "authorization" in request.headers:
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
        return httpx.Response(200, json={"public": True})

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    assert lookup.get("https://api.github.com/repos/public/repository").status == "rate_limited"
    assert lookup.get("https://api.github.com/user").status == "ok"
    assert len(observed) == 2
    assert "api.github.com:authenticated" in lookup._cooldown


def test_authenticated_retry_does_not_reuse_anonymous_cached_failure(monkeypatch):
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json={"private": False, "full_name": "public/repository"}) if (
            "authorization" in request.headers
        ) else httpx.Response(403, headers={"x-ratelimit-remaining": "0"})

    lookup, settings = lookup_with_transport(monkeypatch, handler, token="")
    url = "https://api.github.com/repos/public/repository"
    assert lookup.get(url).status == "rate_limited"
    settings.github_token = SecretStr(_TEST_TOKEN)
    answer = lookup.get(url)
    assert answer.status == "ok" and answer.cached is False and len(observed) == 2


@pytest.mark.parametrize("full_name", ["../repository", "public/..", "public/repository/extra", "not a repo"])
def test_invalid_canonical_full_name_is_never_promoted_to_public_alias(monkeypatch, full_name):
    lookup, _ = lookup_with_transport(monkeypatch, lambda _request: httpx.Response(
        200, json={"private": False, "full_name": full_name}))
    lookup.get("https://api.github.com/repos/old/repository")
    assert lookup._public_repositories == {"old/repository"}


def archived_repository_proof(*, kind="public_code_response", requested=None, final=None,
                              status="ok", http_status=200, data=None, truncated=False):
    from app.credibility.snapshots import capture_snapshot

    payload = data if data is not None else {"id": 123, "private": False, "full_name": "new/repository"}
    return capture_snapshot(
        kind=kind, raw=json.dumps(payload).encode(), normalized_json=payload,
        requested_url=requested or "https://api.github.com/repos/old/repository",
        final_url=final or "https://api.github.com/repositories/123",
        fetch_status=status, http_status=http_status, truncated=truncated,
    )


def test_cold_lookup_restores_verified_metadata_proof_and_reads_comments_without_refetch(monkeypatch):
    from app.credibility.snapshots import derive_snapshot

    observed = []

    def handler(request):
        observed.append(request)
        assert request.url.path == "/repos/old/repository/issues/186/comments"
        assert request.headers["authorization"] == "Bearer " + _TEST_TOKEN
        return httpx.Response(200, json=[{"body": "public comment"}])

    response_ref = archived_repository_proof()
    source_ref = derive_snapshot(response_ref, kind="repository", metadata={"source_id": "old_source"})
    cold, _ = lookup_with_transport(monkeypatch, handler)
    assert not cold._public_repositories
    assert cold.restore_public_repository_proof(source_ref) is True
    assert cold._public_repositories == {"old/repository", "new/repository"}
    assert cold.get("https://api.github.com/repos/old/repository/issues/186/comments").status == "ok"
    assert len(observed) == 1


@pytest.mark.parametrize("kwargs", [
    {"kind": "project_page"},
    {"requested": "https://example.org/repos/old/repository"},
    {"requested": "https://api.github.com/repos/old/repository/issues/186"},
    {"requested": "https://api.github.com/repositories/123"},
    {"requested": "https://api.github.com/repos/old/repository?forged=1"},
    {"status": "rate_limited", "http_status": 403},
    {"http_status": 201},
    {"truncated": True},
    {"data": {"id": 123, "private": True, "full_name": "new/repository"}},
    {"data": {"id": 999, "private": False, "full_name": "new/repository"}},
    {"data": {"id": 123, "private": False, "full_name": "../repository"}},
    {"final": "https://evil.example/repositories/123"},
    {"final": "https://api.github.com/repositories/123/readme"},
    {"final": "https://api.github.com/repos/unrelated/repository"},
])
def test_cold_auth_proof_rejects_webpage_issue_private_or_mismatched_metadata(kwargs):
    lookup = PublicCodeLookup(interval=0)
    assert lookup.restore_public_repository_proof(archived_repository_proof(**kwargs)) is False
    assert not lookup._public_repositories


def test_cold_auth_proof_rejects_tampered_hash_and_arbitrary_repository_string():
    from app.credibility.snapshots import SnapshotError, SnapshotStore

    lookup = PublicCodeLookup(interval=0)
    ref = archived_repository_proof()
    snapshot = read_snapshot(ref)
    SnapshotStore()._path(snapshot.manifest["blobs"]["raw"]["sha256"]).write_bytes(b"changed")
    with pytest.raises(SnapshotError, match="integrity"):
        lookup.restore_public_repository_proof(ref)
    with pytest.raises(SnapshotError, match="reference"):
        lookup.restore_public_repository_proof("old/repository")
    assert not lookup._public_repositories


def test_cold_auth_proof_requires_original_raw_response_to_agree_with_normalized_metadata():
    from app.credibility.snapshots import capture_snapshot

    lookup = PublicCodeLookup(interval=0)
    ref = capture_snapshot(
        kind="public_code_response", raw=b'{"private":true}',
        normalized_json={"id": 123, "private": False, "full_name": "new/repository"},
        requested_url="https://api.github.com/repos/old/repository",
        final_url="https://api.github.com/repositories/123", fetch_status="ok", http_status=200,
    )
    assert lookup.restore_public_repository_proof(ref) is False
    assert not lookup._public_repositories


@pytest.mark.parametrize("path", [
    "/repos/../user", "/repos/./user", "/repos/public/..", "/repos/public/.",
    "/repos/public/repository/../../../user", "/repos/public/repository/./contents/secret.py",
    "/repos/public/repository/%2e%2e/%2e%2e/%2e%2e/user",
])
def test_dot_segment_normalization_never_transfers_repository_token_to_other_api_paths(monkeypatch, path):
    observed = []

    def handler(request):
        observed.append(request)
        if request.url.path == "/repos/public/repository":
            return httpx.Response(200, json={"private": False, "full_name": "public/repository"})
        return httpx.Response(200, json={"public": True})

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    lookup.get("https://api.github.com/repos/public/repository")
    assert lookup._repository_path("https://api.github.com" + path) is None
    lookup.get("https://api.github.com" + path)
    assert len(observed) == 2 and "authorization" not in observed[-1].headers


@pytest.mark.parametrize("name", [".github", "repo.with.dots", "repo..name"])
def test_regular_dots_in_repository_names_remain_valid_metadata_paths(monkeypatch, name):
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json={"private": False, "full_name": "public/" + name})

    lookup, _ = lookup_with_transport(monkeypatch, handler)
    assert lookup.get("https://api.github.com/repos/public/" + name).status == "ok"
    assert "authorization" in observed[0].headers
