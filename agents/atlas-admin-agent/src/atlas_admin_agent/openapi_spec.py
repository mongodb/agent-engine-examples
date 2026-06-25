"""Fetch and query the MongoDB Atlas Administration API OpenAPI spec.

MongoDB publishes one OpenAPI file per dated API version under
``github.com/mongodb/openapi`` — e.g. ``openapi/v2/openapi-2025-03-12.json``.
Each file is pre-resolved against a single wire version: every operation
has exactly one request-body content-type, exactly matching the wire
behavior Atlas serves to clients sending that ``Accept`` header.

We pin our fetch to the same date our :data:`AtlasClient` sends. That way
the schema the tool hands to the LLM is literally the schema Atlas will
validate against on the wire — no client-side version resolution, no
chance of drifting away from the runtime behavior.

To bump the wire version:
  1. Update ``atlas_client.DEFAULT_API_VERSION_ACCEPT``
  2. Update :data:`ATLAS_SPEC_VERSION` in this file to the same date
  3. Run the test suite; it pins both values together.

The spec is ~4 MB. We fetch it once per process on demand and cache it.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# The dated Atlas API version this agent pins against. Must match
# ``atlas_client.DEFAULT_API_VERSION_ACCEPT``. When you bump one, bump both
# (there's a test that checks they agree).
ATLAS_SPEC_VERSION = "2025-03-12"

# Canonical home for the spec: https://github.com/mongodb/openapi
# Each dated file is the spec pre-resolved against that wire version.
ATLAS_OPENAPI_URL = (
    f"https://raw.githubusercontent.com/mongodb/openapi/main/"
    f"openapi/v2/openapi-{ATLAS_SPEC_VERSION}.json"
)

# Atlas paths in the spec are prefixed with /api/atlas/v2. Our agent refers to
# paths without that prefix (e.g. "/groups/{id}/clusters"), matching how users
# and our existing tools talk about them.
SPEC_PATH_PREFIX = "/api/atlas/v2"

_lock = threading.Lock()
_spec_cache: dict[str, Any] | None = None


class SpecLookupError(RuntimeError):
    """Raised when the spec cannot be fetched or an operation cannot be resolved."""


def _fetch_spec() -> dict[str, Any]:
    global _spec_cache
    with _lock:
        if _spec_cache is not None:
            return _spec_cache
        try:
            response = httpx.get(ATLAS_OPENAPI_URL, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SpecLookupError(f"failed to fetch Atlas OpenAPI spec: {exc}") from exc
        spec = response.json()
        _spec_cache = spec
        logger.info(
            "loaded Atlas OpenAPI spec: version=%s, paths=%d",
            spec.get("info", {}).get("version"),
            len(spec.get("paths", {})),
        )
        return spec


def _reset_cache_for_tests() -> None:
    global _spec_cache
    with _lock:
        _spec_cache = None


def _path_candidates(user_path: str) -> list[str]:
    """Return spec-path candidates for a user-supplied path.

    The spec uses ``/api/atlas/v2/groups/{groupId}/clusters`` while users
    write ``/groups/{id}/clusters``. We normalize both ends.
    """
    p = user_path if user_path.startswith("/") else f"/{user_path}"
    candidates = [p]
    if not p.startswith(SPEC_PATH_PREFIX):
        candidates.append(f"{SPEC_PATH_PREFIX}{p}")
    return candidates


def _match_path(user_path: str, spec_paths: dict[str, Any]) -> str | None:
    """Find the spec path template that matches the user's concrete path.

    Exact-match the template form (placeholders in the same positions), and
    also accept when the user passed a real ID in place of a spec placeholder:
    ``/groups/{groupId}/clusters/{clusterName}`` should match
    ``/groups/64f0d.../clusters/Cluster0``.
    """
    for candidate in _path_candidates(user_path):
        # Exact template match wins.
        if candidate in spec_paths:
            return candidate
        # Try matching by shape, replacing concrete segments with {placeholder}.
        for template in spec_paths:
            if _path_shapes_match(candidate, template):
                return template
    return None


def _path_shapes_match(concrete: str, template: str) -> bool:
    # Strip leading/trailing slashes and split, comparing segment counts and
    # literal segments (non-placeholder) for equality.
    c_parts = concrete.strip("/").split("/")
    t_parts = template.strip("/").split("/")
    if len(c_parts) != len(t_parts):
        return False
    for c, t in zip(c_parts, t_parts):
        if t.startswith("{") and t.endswith("}"):
            continue  # placeholder — anything matches
        if c != t:
            return False
    return True


def _resolve_ref(spec: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SpecLookupError(f"only local refs are supported, got {ref!r}")
    node: Any = spec
    for segment in ref.lstrip("#/").split("/"):
        if not isinstance(node, dict) or segment not in node:
            raise SpecLookupError(f"ref {ref!r} could not be resolved")
        node = node[segment]
    if not isinstance(node, dict):
        raise SpecLookupError(f"ref {ref!r} did not resolve to an object")
    return node


def _inline_schema(
    spec: dict[str, Any],
    schema: Any,
    *,
    depth: int = 0,
    max_depth: int = 12,
    for_write: bool = False,
) -> Any:
    """Recursively resolve ``$ref`` entries into an inline schema.

    Also flattens ``allOf`` composition into a single merged object schema —
    without this the consumer has to walk an ``allOf`` list and merge the
    pieces themselves, which LLMs handle poorly. ``oneOf``/``anyOf`` are
    inlined per-branch but not merged (they're genuinely alternatives).

    When ``for_write`` is true, skip ``readOnly`` properties — they're
    server-computed and don't belong in a request body.
    """
    if not isinstance(schema, dict):
        return schema
    if depth > max_depth:
        return {"$ref_depth_limit": True}

    if "$ref" in schema:
        try:
            resolved = _resolve_ref(spec, schema["$ref"])
        except SpecLookupError:
            return schema
        return _inline_schema(
            spec, resolved, depth=depth + 1, max_depth=max_depth, for_write=for_write
        )

    # Skip this subtree entirely if it's server-computed on a write request.
    if for_write and schema.get("readOnly"):
        return None

    # Flatten allOf into this level: merge properties, required, and type
    # from every piece. This collapses the Atlas "CloudRegionConfig20240805
    # + AWSRegionConfig20240805 addons" pattern into one schema the LLM
    # can read top-down.
    if "allOf" in schema and isinstance(schema["allOf"], list):
        merged = _merge_all_of(spec, schema, depth=depth, max_depth=max_depth, for_write=for_write)
        if merged is not None:
            return merged

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in ("properties", "patternProperties"):
            inlined_props: dict[str, Any] = {}
            for k, v in value.items():
                sub = _inline_schema(
                    spec, v, depth=depth + 1, max_depth=max_depth, for_write=for_write
                )
                if sub is None:
                    continue  # readOnly stripped
                inlined_props[k] = sub
            out[key] = inlined_props
        elif key in ("items", "additionalProperties", "not"):
            out[key] = _inline_schema(
                spec, value, depth=depth + 1, max_depth=max_depth, for_write=for_write
            )
        elif key in ("oneOf", "anyOf"):
            out[key] = [
                _inline_schema(spec, v, depth=depth + 1, max_depth=max_depth, for_write=for_write)
                for v in value
            ]
        else:
            out[key] = value
    return out


def _merge_all_of(
    spec: dict[str, Any],
    schema: dict[str, Any],
    *,
    depth: int,
    max_depth: int,
    for_write: bool,
) -> dict[str, Any] | None:
    """Flatten an ``allOf`` composition into a single schema dict."""
    pieces = schema["allOf"]
    inlined_pieces: list[dict[str, Any]] = []
    for piece in pieces:
        inlined = _inline_schema(
            spec, piece, depth=depth + 1, max_depth=max_depth, for_write=for_write
        )
        if isinstance(inlined, dict):
            inlined_pieces.append(inlined)

    # Start from any sibling keys on the allOf parent (description, type, etc.)
    merged: dict[str, Any] = {k: v for k, v in schema.items() if k != "allOf"}
    # Recurse into siblings' own refs/allOfs.
    for k in list(merged):
        if k in ("properties", "patternProperties"):
            merged[k] = {
                name: _inline_schema(
                    spec, sub, depth=depth + 1, max_depth=max_depth, for_write=for_write
                )
                for name, sub in merged[k].items()
            }

    merged_props: dict[str, Any] = dict(merged.get("properties") or {})
    merged_required: list[str] = list(merged.get("required") or [])
    for piece in inlined_pieces:
        for k, v in piece.items():
            if k == "properties" and isinstance(v, dict):
                for name, sub in v.items():
                    if sub is None:
                        continue
                    # On write, we might have already stripped a readOnly field
                    # in the piece; don't re-add as None.
                    merged_props[name] = sub
            elif k == "required" and isinstance(v, list):
                for name in v:
                    if name not in merged_required:
                        merged_required.append(name)
            elif k not in merged:
                # Don't let pieces overwrite parent-level fields like description
                # that we deliberately kept.
                merged[k] = v

    if merged_props:
        merged["properties"] = merged_props
    if merged_required:
        merged["required"] = merged_required
    return merged


def describe_endpoint(method: str, path: str) -> dict[str, Any]:
    """Return a structured schema description for an Atlas operation.

    Raises :class:`SpecLookupError` if the spec cannot be fetched or the
    operation cannot be found. On success returns a dict with:

    - ``operationId`` — the Atlas operation name (e.g. ``createCluster``)
    - ``summary`` / ``description`` — human prose from the spec
    - ``path`` — the spec's path template (with ``{groupId}`` etc.)
    - ``method`` — uppercased HTTP method
    - ``resolved_content_type`` — the request-body content-type from the
      spec (always a single dated Atlas content-type for this spec file)
    - ``request_body_schema`` — inlined JSON schema (refs resolved, bounded
      depth), or ``None`` if the operation takes no body
    - ``parameters`` — path/query params the operation declares
    - ``spec_version`` — the spec's ``info.version``
    """
    spec = _fetch_spec()
    method_u = method.upper()

    template = _match_path(path, spec.get("paths", {}))
    if template is None:
        raise SpecLookupError(f"no operation found for path {path!r}")

    path_item = spec["paths"][template]
    operation = path_item.get(method_u.lower())
    if operation is None:
        raise SpecLookupError(
            f"path {template!r} has no {method_u} operation; "
            f"methods available: {[m.upper() for m in path_item if m in ('get', 'post', 'put', 'patch', 'delete')]}"
        )

    # Each operation in a version-pinned spec file has a single content-type
    # (the one Atlas serves for this wire date). Just pick it.
    content = (operation.get("requestBody") or {}).get("content") or {}
    schema_body: dict[str, Any] | None = None
    resolved_ct: str | None = None
    is_write_method = method_u in ("POST", "PUT", "PATCH", "DELETE")
    if content:
        resolved_ct = next(iter(content))
        schema = (content[resolved_ct] or {}).get("schema") or {}
        schema_body = _inline_schema(spec, schema, for_write=is_write_method)

    # Parameters (path + query). We inline each.
    parameters = []
    for param in (operation.get("parameters") or []) + (path_item.get("parameters") or []):
        if isinstance(param, dict) and "$ref" in param:
            param = _resolve_ref(spec, param["$ref"])
        if isinstance(param, dict):
            parameters.append(
                {
                    "name": param.get("name"),
                    "in": param.get("in"),
                    "required": param.get("required", False),
                    "description": param.get("description"),
                    "schema": _inline_schema(spec, param.get("schema") or {}),
                }
            )

    example_body = _example_from_schema(schema_body) if schema_body else None

    return {
        "operationId": operation.get("operationId"),
        "summary": operation.get("summary"),
        "description": operation.get("description"),
        "method": method_u,
        "path": template,
        "resolved_content_type": resolved_ct,
        "request_body_schema": schema_body,
        "example_body": example_body,
        "parameters": parameters,
        "spec_version": spec.get("info", {}).get("version"),
        "spec_source": ATLAS_OPENAPI_URL,
    }


def _example_from_schema(schema: Any, *, depth: int = 0, max_depth: int = 10) -> Any:
    """Synthesize an example body from a schema, preferring required fields.

    Strategy:
    - Honor ``example`` and ``default`` when the spec provides them.
    - For objects, emit every ``required`` property. If ``required`` is
      empty or absent, fall back to emitting every declared property — for
      Atlas this is much more useful than returning nothing, because the
      spec frequently omits required arrays even though the API enforces
      them at runtime.
    - If the schema declares ``properties`` AND ``oneOf``/``anyOf``, merge
      them: take the base properties and fold in the first alternative that
      adds concrete fields. Atlas uses this pattern for e.g.
      ``regionConfigs`` where the base carries provider-agnostic fields and
      the oneOf adds AWS/Azure/GCP specifics.
    - If the schema has ONLY ``oneOf``/``anyOf``, pick the first variant
      that yields non-empty content.
    """
    if not isinstance(schema, dict) or depth > max_depth:
        return None

    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]

    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)

    # Start with the base object (if any).
    base: dict[str, Any] = {}
    has_base_object = t == "object" or "properties" in schema
    if has_base_object:
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        names = list(required) if required else list(props.keys())
        for name in names:
            sub = props.get(name)
            if sub is None:
                continue
            value = _example_from_schema(sub, depth=depth + 1, max_depth=max_depth)
            if value is not None:
                base[name] = value

    # Fold in oneOf/anyOf alternatives. Keep trying alternatives until one
    # produces something, then merge its keys into base. This is crucial for
    # Atlas's hardware-spec pattern (base has diskSizeGB; oneOf adds
    # instanceSize and nodeCount).
    for key in ("oneOf", "anyOf"):
        alternatives = schema.get(key)
        if not isinstance(alternatives, list) or not alternatives:
            continue
        for alt in alternatives:
            alt_value = _example_from_schema(alt, depth=depth + 1, max_depth=max_depth)
            if isinstance(alt_value, dict) and alt_value:
                for k, v in alt_value.items():
                    base.setdefault(k, v)
                break
            if alt_value is not None and not base:
                # Non-dict alternative (e.g., a string/number). Only use it
                # if we have no base yet.
                return alt_value
        # Only consume one composition keyword.
        break

    if has_base_object or base:
        return base or None

    if t == "array":
        item = _example_from_schema(schema.get("items") or {}, depth=depth + 1, max_depth=max_depth)
        return [item] if item is not None else []
    if t == "string":
        return schema.get("enum", ["..."])[0] if schema.get("enum") else "..."
    if t == "integer" or t == "number":
        return schema.get("enum", [0])[0] if schema.get("enum") else 0
    if t == "boolean":
        return schema.get("enum", [False])[0] if schema.get("enum") else False
    return None
