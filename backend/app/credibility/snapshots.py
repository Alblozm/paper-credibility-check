"""Immutable, content-addressed evidence snapshots shared by credibility stages.

References are opaque and portable: they contain a manifest digest, never a local
path. Each observation has its own manifest; identical response/input blobs are
stored only once. Reading always verifies every referenced blob before use.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings

_REF = re.compile(r"snapshot:v1:([0-9a-f]{64})\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class SnapshotError(ValueError):
    """A snapshot is unavailable, malformed or no longer matches its digest."""


@dataclass(frozen=True)
class Snapshot:
    manifest: dict[str, Any]
    raw: bytes | None
    normalized_json: Any = None
    normalized_text: str | None = None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def artifact_root() -> Path:
    """Resolve on demand so deployments/tests can isolate their artifact storage."""
    configured = os.environ.get("CREDIBILITY_ARTIFACT_DIR")
    return Path(configured) if configured else Path(get_settings().upload_dir).parent / "credibility-artifacts"


class SnapshotStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else artifact_root()

    def _path(self, digest: str) -> Path:
        if not _DIGEST.fullmatch(digest):
            raise SnapshotError("Invalid snapshot content digest.")
        return self.root / "objects" / "sha256" / digest[:2] / digest

    def _read_blob(self, digest: str) -> bytes:
        path = self._path(digest)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise SnapshotError(f"Snapshot content is missing or unreadable: {digest}.") from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise SnapshotError(f"Snapshot integrity check failed: {digest}.")
        return data

    def _put_blob(self, data: bytes) -> dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Hard-link publication is atomic and never overwrites an existing blob.
        # A crash can leave an unreferenced temp file, never a partial snapshot.
        fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                self._read_blob(digest)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"sha256": digest, "size_bytes": len(data)}

    def record(
        self,
        *,
        kind: str,
        raw: bytes | None = None,
        normalized_json: Any = None,
        normalized_text: str | None = None,
        requested_url: str | None = None,
        final_url: str | None = None,
        retrieved_at: str | None = None,
        observed_at: str | None = None,
        fetch_status: str | None = None,
        http_status: int | None = None,
        is_cached: bool = False,
        truncated: bool = False,
        revision: str | None = None,
        path: str | None = None,
        metadata: dict[str, Any] | None = None,
        parent_ref: str | None = None,
    ) -> str:
        if raw is not None and not isinstance(raw, bytes):
            raise TypeError("Raw snapshot content must be bytes.")
        if normalized_text is not None and not isinstance(normalized_text, str):
            raise TypeError("Normalized text must be a string.")
        if parent_ref is not None:
            self.read(parent_ref)
        blobs: dict[str, Any] = {}
        if raw is not None:
            blobs["raw"] = self._put_blob(raw)
        if normalized_json is not None:
            blobs["normalized_json"] = self._put_blob(_json_bytes(normalized_json))
        if normalized_text is not None:
            blobs["normalized_text"] = self._put_blob(normalized_text.encode("utf-8"))
        manifest = {
            "schema_version": "credibility-snapshot-1",
            "observation_id": uuid.uuid4().hex,
            "kind": kind,
            "requested_url": requested_url,
            "final_url": final_url,
            "retrieved_at": retrieved_at,
            "observed_at": observed_at or _now(),
            "fetch_status": fetch_status,
            "http_status": http_status,
            "is_cached": is_cached,
            "truncated": truncated,
            "revision": revision,
            "path": path,
            "parent_ref": parent_ref,
            "metadata": metadata or {},
            "blobs": blobs,
        }
        digest = self._put_blob(_json_bytes(manifest))["sha256"]
        return f"snapshot:v1:{digest}"

    def read(self, ref: str) -> Snapshot:
        match = _REF.fullmatch(ref) if isinstance(ref, str) else None
        if not match:
            raise SnapshotError("Invalid snapshot reference.")
        try:
            manifest = json.loads(self._read_blob(match[1]))
            if not isinstance(manifest, dict) or manifest.get("schema_version") != "credibility-snapshot-1":
                raise SnapshotError("Unsupported snapshot schema.")
            if manifest.get("parent_ref"):
                self.read(manifest["parent_ref"])
            if not isinstance(manifest.get("blobs"), dict) or not isinstance(manifest.get("metadata"), dict):
                raise SnapshotError("Malformed snapshot blob descriptors or metadata.")
            values = {}
            for name, descriptor in manifest["blobs"].items():
                if name not in {"raw", "normalized_json", "normalized_text"}:
                    raise SnapshotError("Unknown snapshot content kind.")
                data = self._read_blob(descriptor["sha256"])
                if len(data) != descriptor["size_bytes"]:
                    raise SnapshotError("Snapshot content length does not match its manifest.")
                values[name] = (
                    json.loads(data) if name == "normalized_json" else data.decode("utf-8")
                    if name == "normalized_text" else data
                )
            return Snapshot(
                manifest=manifest, raw=values.get("raw"),
                normalized_json=values.get("normalized_json"), normalized_text=values.get("normalized_text"),
            )
        except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise SnapshotError("Malformed snapshot manifest or content.") from exc

    def derive(self, ref: str, **overrides: Any) -> str:
        """Record another observation/source with the same verified content."""
        previous = self.read(ref)
        keys = ("kind", "requested_url", "final_url", "retrieved_at", "fetch_status", "http_status", "is_cached",
                "truncated", "revision", "path", "metadata")
        arguments = {key: previous.manifest.get(key) for key in keys}
        arguments.update(raw=previous.raw, normalized_json=previous.normalized_json,
                         normalized_text=previous.normalized_text, parent_ref=ref)
        arguments.update(overrides)
        arguments["metadata"] = {**previous.manifest.get("metadata", {}), **overrides.get("metadata", {})}
        return self.record(**arguments)


def capture_snapshot(**kwargs: Any) -> str:
    return SnapshotStore().record(**kwargs)


def read_snapshot(ref: str) -> Snapshot:
    return SnapshotStore().read(ref)


def derive_snapshot(ref: str, **overrides: Any) -> str:
    return SnapshotStore().derive(ref, **overrides)


def verify_report_snapshots(report: Any) -> dict[str, Any]:
    """Verify report source archives without networking or re-running any checks.

    Quote semantics are validated by each producing check; this verifies the
    archived source identity, fetch state and every referenced content digest.
    """
    sources = report.get("sources", []) if isinstance(report, dict) else report.sources
    verified: list[str] = []
    for source in sources:
        value = source if isinstance(source, dict) else source.model_dump(mode="json")
        ref = value.get("snapshot_ref")
        if not ref:
            raise SnapshotError(f"Source {value.get('source_id')} has no snapshot reference.")
        snapshot = read_snapshot(ref)
        manifest = snapshot.manifest
        metadata = manifest.get("metadata", {})
        for field in ("fetch_status", "http_status", "is_cached"):
            if manifest.get(field) != value.get(field):
                raise SnapshotError(f"Source {value.get('source_id')} does not match snapshot {field}.")
        if metadata.get("source_id") not in (None, value.get("source_id")):
            raise SnapshotError("Source identity does not match its snapshot.")
        source_url = metadata.get("source_url") or manifest.get("requested_url")
        if source_url and source_url != value.get("url"):
            raise SnapshotError("Source URL does not match its snapshot.")
        verified.append(ref)
    discovery = report.get("code_discovery") if isinstance(report, dict) else getattr(report, "code_discovery", None)
    if discovery:
        discovery = discovery if isinstance(discovery, dict) else discovery.model_dump(mode="json")
        if discovery.get("audit_ref"):
            _verify_discovery_audit(discovery["audit_ref"])
    return {"source_count": len(sources), "verified_count": len(verified), "snapshot_refs": verified}


def _verify_discovery_audit(ref: str) -> None:
    """Follow the finite saved provenance graph, without any provider calls."""
    seen = set()

    def visit(reference):
        if reference in seen:
            return
        seen.add(reference)
        saved = read_snapshot(reference)
        walk(saved.normalized_json)

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if (key.endswith("snapshot_ref") or key.endswith("audit_ref")) and isinstance(item, str):
                    visit(item)
                elif key.endswith("snapshot_refs") and isinstance(item, list):
                    for reference in item:
                        if isinstance(reference, str):
                            visit(reference)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    saved = read_snapshot(ref)
    if saved.manifest.get("kind") != "code_discovery_audit":
        raise SnapshotError("Discovery audit reference has the wrong snapshot kind.")
    visit(ref)
