"""Tests for ``atlas_admin_agent.openapi_spec``.

The real spec is ~4 MB and fetched from GitHub; tests stub it with a
synthetic spec via ``httpx.MockTransport`` hooked into ``httpx.get``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from atlas_admin_agent import openapi_spec


# --- fixtures -------------------------------------------------------------


def _sample_spec() -> dict:
    """A tiny spec shaped like a real version-pinned Atlas spec file.

    Every operation carries exactly one ``application/vnd.atlas.<date>+json``
    content-type — that's the key difference from the monolithic ``v2.json``
    (which carried all historical versions per op).
    """
    return {
        "openapi": "3.0.1",
        "info": {"title": "MongoDB Atlas Administration API", "version": "2.0"},
        "paths": {
            "/api/atlas/v2/groups/{groupId}/clusters": {
                "post": {
                    "operationId": "createGroupCluster",
                    "summary": "Create one cluster in one project",
                    "description": "Creates a cluster.",
                    "parameters": [
                        {
                            "name": "groupId",
                            "in": "path",
                            "required": True,
                            "description": "Unique 24-hex project id.",
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/vnd.atlas.2024-10-23+json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ClusterDescription20240805"
                                }
                            },
                        }
                    },
                },
                "get": {
                    "operationId": "listGroupClusters",
                    "summary": "List clusters",
                    "parameters": [
                        {
                            "name": "groupId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                },
            },
            "/api/atlas/v2/groups/{groupId}/clusters/{clusterName}": {
                "patch": {
                    "operationId": "updateCluster",
                    "summary": "Update one cluster",
                    "requestBody": {
                        "content": {
                            "application/vnd.atlas.2024-10-23+json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "paused": {"type": "boolean"},
                                        "replicationSpecs": {
                                            "type": "array",
                                            "items": {
                                                "$ref": ("#/components/schemas/ReplicationSpec")
                                            },
                                        },
                                    },
                                }
                            }
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "ClusterDescription20240805": {
                    "type": "object",
                    "description": "v2 cluster shape",
                    "properties": {
                        "name": {"type": "string"},
                        "clusterType": {"type": "string"},
                        "replicationSpecs": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/ReplicationSpec"},
                        },
                    },
                },
                "ReplicationSpec": {
                    "type": "object",
                    "properties": {
                        "regionConfigs": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "providerName": {"type": "string"},
                                    "regionName": {"type": "string"},
                                    "electableSpecs": {"type": "object"},
                                },
                            },
                        }
                    },
                },
            }
        },
    }


@pytest.fixture(autouse=True)
def _reset_cache_and_stub_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    openapi_spec._reset_cache_for_tests()
    spec_bytes = json.dumps(_sample_spec()).encode()

    def fake_get(url, *, timeout=None, follow_redirects=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, content=spec_bytes, request=request)

    monkeypatch.setattr(openapi_spec.httpx, "get", fake_get)


# --- basic resolution -----------------------------------------------------


def test_describe_endpoint_resolves_v2_schema() -> None:
    result = openapi_spec.describe_endpoint("POST", "/groups/{groupId}/clusters")
    assert result["operationId"] == "createGroupCluster"
    assert result["method"] == "POST"
    # Version-pinned spec file carries exactly one content-type per op.
    assert result["resolved_content_type"] == "application/vnd.atlas.2024-10-23+json"
    schema = result["request_body_schema"]
    assert schema["description"] == "v2 cluster shape"
    # nested ref under replicationSpecs also resolved
    assert (
        schema["properties"]["replicationSpecs"]["items"]["properties"]["regionConfigs"]["items"][
            "properties"
        ]["providerName"]["type"]
        == "string"
    )


def test_spec_url_is_version_pinned() -> None:
    # The URL must embed the dated version, not point at the monolithic v2.json.
    assert openapi_spec.ATLAS_SPEC_VERSION in openapi_spec.ATLAS_OPENAPI_URL
    assert "/v2/openapi-" in openapi_spec.ATLAS_OPENAPI_URL
    assert openapi_spec.ATLAS_OPENAPI_URL.endswith(".json")


def test_spec_version_matches_atlas_client_accept_header() -> None:
    # If someone bumps the wire version without bumping the spec pin (or
    # vice versa), this test fires. Keeps schema lookup aligned with the
    # actual bytes Atlas validates against.
    from atlas_admin_agent.atlas_client import DEFAULT_API_VERSION_ACCEPT

    assert openapi_spec.ATLAS_SPEC_VERSION in DEFAULT_API_VERSION_ACCEPT, (
        f"ATLAS_SPEC_VERSION ({openapi_spec.ATLAS_SPEC_VERSION}) must match the "
        f"date in DEFAULT_API_VERSION_ACCEPT ({DEFAULT_API_VERSION_ACCEPT})"
    )


# --- path matching -------------------------------------------------------


def test_describe_endpoint_accepts_concrete_paths() -> None:
    # User sent a path with real IDs, not the template form.
    result = openapi_spec.describe_endpoint(
        "PATCH",
        "/groups/64f0d460ce4e047320892068/clusters/Cluster0",
    )
    assert result["operationId"] == "updateCluster"
    assert result["path"] == "/api/atlas/v2/groups/{groupId}/clusters/{clusterName}"


def test_describe_endpoint_accepts_spec_prefix_already_present() -> None:
    result = openapi_spec.describe_endpoint("POST", "/api/atlas/v2/groups/{groupId}/clusters")
    assert result["operationId"] == "createGroupCluster"


def test_describe_endpoint_accepts_path_without_leading_slash() -> None:
    result = openapi_spec.describe_endpoint("POST", "groups/{groupId}/clusters")
    assert result["operationId"] == "createGroupCluster"


def test_describe_endpoint_raises_for_unknown_path() -> None:
    with pytest.raises(openapi_spec.SpecLookupError) as excinfo:
        openapi_spec.describe_endpoint("GET", "/groups/{groupId}/nonexistent")
    assert "no operation found" in str(excinfo.value)


def test_describe_endpoint_raises_for_wrong_method() -> None:
    # The sample spec only defines POST and GET on /clusters.
    with pytest.raises(openapi_spec.SpecLookupError) as excinfo:
        openapi_spec.describe_endpoint("PUT", "/groups/{groupId}/clusters")
    msg = str(excinfo.value)
    assert "PUT" in msg
    # And it lists the methods that _are_ available to help the caller.
    assert "POST" in msg
    assert "GET" in msg


# --- parameters ----------------------------------------------------------


def test_describe_endpoint_includes_path_parameters() -> None:
    result = openapi_spec.describe_endpoint("POST", "/groups/{groupId}/clusters")
    params = result["parameters"]
    assert len(params) == 1
    assert params[0]["name"] == "groupId"
    assert params[0]["in"] == "path"
    assert params[0]["required"] is True
    assert "24-hex" in (params[0]["description"] or "")


def test_describe_endpoint_no_body_operation_returns_none_schema() -> None:
    result = openapi_spec.describe_endpoint("GET", "/groups/{groupId}/clusters")
    assert result["request_body_schema"] is None


# --- caching + errors ----------------------------------------------------


def test_spec_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def counting_get(url, *, timeout=None, follow_redirects=None):
        calls.append(1)
        request = httpx.Request("GET", url)
        return httpx.Response(200, content=json.dumps(_sample_spec()).encode(), request=request)

    openapi_spec._reset_cache_for_tests()
    monkeypatch.setattr(openapi_spec.httpx, "get", counting_get)

    openapi_spec.describe_endpoint("POST", "/groups/{groupId}/clusters")
    openapi_spec.describe_endpoint("GET", "/groups/{groupId}/clusters")
    openapi_spec.describe_endpoint("PATCH", "/groups/{groupId}/clusters/{clusterName}")

    assert len(calls) == 1  # fetched only once


def test_network_error_raises_spec_lookup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_get(url, *, timeout=None, follow_redirects=None):
        raise httpx.ConnectError("no network")

    openapi_spec._reset_cache_for_tests()
    monkeypatch.setattr(openapi_spec.httpx, "get", broken_get)

    with pytest.raises(openapi_spec.SpecLookupError) as excinfo:
        openapi_spec.describe_endpoint("POST", "/groups/{groupId}/clusters")
    assert "Atlas OpenAPI spec" in str(excinfo.value)


def test_http_error_raises_spec_lookup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_get(url, *, timeout=None, follow_redirects=None):
        request = httpx.Request("GET", url)
        return httpx.Response(500, content=b"server error", request=request)

    openapi_spec._reset_cache_for_tests()
    monkeypatch.setattr(openapi_spec.httpx, "get", failing_get)

    with pytest.raises(openapi_spec.SpecLookupError):
        openapi_spec.describe_endpoint("POST", "/groups/{groupId}/clusters")


# --- ref resolution + depth cap ------------------------------------------


def test_local_ref_is_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = {
        "openapi": "3.0.1",
        "info": {"version": "x"},
        "paths": {
            "/api/atlas/v2/foo": {
                "post": {
                    "operationId": "f",
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Foo"}}
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {"Foo": {"type": "object", "properties": {"a": {"type": "string"}}}}
        },
    }
    openapi_spec._reset_cache_for_tests()
    monkeypatch.setattr(
        openapi_spec.httpx,
        "get",
        lambda url, timeout=None, follow_redirects=None: httpx.Response(
            200, content=json.dumps(spec).encode(), request=httpx.Request("GET", url)
        ),
    )

    result = openapi_spec.describe_endpoint("POST", "/foo")
    assert result["request_body_schema"]["properties"]["a"]["type"] == "string"


def test_unknown_local_ref_is_left_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = {
        "openapi": "3.0.1",
        "info": {"version": "x"},
        "paths": {
            "/api/atlas/v2/foo": {
                "post": {
                    "operationId": "f",
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}
                        }
                    },
                }
            }
        },
        "components": {"schemas": {}},
    }
    openapi_spec._reset_cache_for_tests()
    monkeypatch.setattr(
        openapi_spec.httpx,
        "get",
        lambda url, timeout=None, follow_redirects=None: httpx.Response(
            200, content=json.dumps(spec).encode(), request=httpx.Request("GET", url)
        ),
    )

    result = openapi_spec.describe_endpoint("POST", "/foo")
    # Missing refs are left intact rather than exploding.
    assert result["request_body_schema"] == {"$ref": "#/components/schemas/Missing"}


def test_inline_schema_enforces_depth_cap() -> None:
    # Self-referential schema. We don't recurse forever.
    spec = {
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {"next": {"$ref": "#/components/schemas/Node"}},
                }
            }
        }
    }
    schema = {"$ref": "#/components/schemas/Node"}
    result = openapi_spec._inline_schema(spec, schema)
    assert isinstance(result, dict)
    assert "properties" in result or "$ref_depth_limit" in result


def test_non_local_ref_raises() -> None:
    spec = {"components": {"schemas": {}}}
    with pytest.raises(openapi_spec.SpecLookupError):
        openapi_spec._resolve_ref(spec, "http://external.example.com/schema.json")


# --- readOnly filter + example_body (the fix for cluster-creation bug) ---


def test_write_methods_strip_readonly_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    # readOnly fields are server-computed and should NOT appear in a
    # request-body schema returned for a POST/PUT/PATCH.
    spec = {
        "openapi": "3.0.1",
        "info": {"version": "2.0"},
        "paths": {
            "/api/atlas/v2/foo": {
                "post": {
                    "operationId": "postFoo",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "id": {"type": "string", "readOnly": True},
                                        "createDate": {
                                            "type": "string",
                                            "readOnly": True,
                                        },
                                    },
                                }
                            }
                        }
                    },
                },
                "get": {
                    "operationId": "getFoo",
                    "parameters": [],
                },
            }
        },
        "components": {"schemas": {}},
    }
    openapi_spec._reset_cache_for_tests()
    monkeypatch.setattr(
        openapi_spec.httpx,
        "get",
        lambda url, timeout=None, follow_redirects=None: httpx.Response(
            200, content=json.dumps(spec).encode(), request=httpx.Request("GET", url)
        ),
    )

    post = openapi_spec.describe_endpoint("POST", "/foo")
    assert "id" not in post["request_body_schema"]["properties"]
    assert "createDate" not in post["request_body_schema"]["properties"]
    assert "name" in post["request_body_schema"]["properties"]


def test_read_methods_keep_readonly_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    # For GETs, readOnly fields ARE meaningful (that's what a read returns),
    # so they must not be stripped.
    spec = {
        "openapi": "3.0.1",
        "info": {"version": "2.0"},
        "paths": {
            "/api/atlas/v2/echo": {
                "post": {
                    "operationId": "postEcho",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string", "readOnly": True},
                                        "payload": {"$ref": "#/components/schemas/Thing"},
                                    },
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Thing": {
                    "type": "object",
                    "properties": {
                        "nested": {"type": "string", "readOnly": True},
                        "keep": {"type": "string"},
                    },
                }
            }
        },
    }
    openapi_spec._reset_cache_for_tests()
    monkeypatch.setattr(
        openapi_spec.httpx,
        "get",
        lambda url, timeout=None, follow_redirects=None: httpx.Response(
            200, content=json.dumps(spec).encode(), request=httpx.Request("GET", url)
        ),
    )

    post = openapi_spec.describe_endpoint("POST", "/echo")
    # Top-level readOnly stripped
    assert "id" not in post["request_body_schema"]["properties"]
    # Nested readOnly stripped too (depth-aware)
    payload = post["request_body_schema"]["properties"]["payload"]
    assert "nested" not in payload["properties"]
    assert "keep" in payload["properties"]


def test_example_body_uses_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = {
        "openapi": "3.0.1",
        "info": {"version": "2.0"},
        "paths": {
            "/api/atlas/v2/foo": {
                "post": {
                    "operationId": "postFoo",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name", "clusterType"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "clusterType": {
                                            "type": "string",
                                            "enum": ["REPLICASET", "SHARDED"],
                                        },
                                        "optional": {"type": "string"},
                                    },
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {"schemas": {}},
    }
    openapi_spec._reset_cache_for_tests()
    monkeypatch.setattr(
        openapi_spec.httpx,
        "get",
        lambda url, timeout=None, follow_redirects=None: httpx.Response(
            200, content=json.dumps(spec).encode(), request=httpx.Request("GET", url)
        ),
    )

    result = openapi_spec.describe_endpoint("POST", "/foo")
    example = result["example_body"]
    assert set(example.keys()) == {"name", "clusterType"}
    assert example["clusterType"] == "REPLICASET"  # first enum
    assert "optional" not in example


def test_example_body_handles_arrays_and_nested_objects() -> None:
    schema = {
        "type": "object",
        "required": ["replicationSpecs"],
        "properties": {
            "replicationSpecs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["regionConfigs"],
                    "properties": {
                        "regionConfigs": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["providerName", "regionName"],
                                "properties": {
                                    "providerName": {
                                        "type": "string",
                                        "enum": ["AWS", "GCP", "AZURE"],
                                    },
                                    "regionName": {"type": "string"},
                                    "priority": {"type": "integer"},
                                },
                            },
                        }
                    },
                },
            }
        },
    }
    example = openapi_spec._example_from_schema(schema)
    assert example == {
        "replicationSpecs": [{"regionConfigs": [{"providerName": "AWS", "regionName": "..."}]}]
    }


def test_allof_is_flattened_into_merged_properties() -> None:
    # Atlas uses allOf heavily (e.g. AWSRegionConfig20240805 = allOf[
    # CloudRegionConfig20240805, { properties: analyticsSpecs, ... } ]).
    # The LLM handles a merged flat schema far better than a nested allOf list.
    spec = {
        "components": {
            "schemas": {
                "Base": {
                    "type": "object",
                    "required": ["providerName"],
                    "properties": {
                        "providerName": {"type": "string"},
                        "priority": {"type": "integer"},
                    },
                },
                "Extensions": {
                    "type": "object",
                    "properties": {
                        "analyticsSpecs": {"type": "object"},
                        "autoScaling": {"type": "object"},
                    },
                },
                "AWSRegionConfig": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Base"},
                        {"$ref": "#/components/schemas/Extensions"},
                    ],
                    "description": "AWS region config",
                },
            }
        }
    }
    # Inline the AWSRegionConfig through the merger.
    merged = openapi_spec._inline_schema(spec, {"$ref": "#/components/schemas/AWSRegionConfig"})

    # No allOf remaining — it's been flattened.
    assert "allOf" not in merged
    # Properties from both pieces live side-by-side.
    assert set(merged["properties"].keys()) == {
        "providerName",
        "priority",
        "analyticsSpecs",
        "autoScaling",
    }
    # Required from the Base piece is preserved.
    assert merged["required"] == ["providerName"]
    # Sibling metadata like description survives.
    assert merged["description"] == "AWS region config"


def test_allof_with_conflicting_required_dedupes() -> None:
    spec = {
        "components": {
            "schemas": {
                "Foo": {
                    "allOf": [
                        {
                            "type": "object",
                            "required": ["a", "b"],
                            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
                        },
                        {
                            "type": "object",
                            "required": ["b", "c"],
                            "properties": {"c": {"type": "boolean"}},
                        },
                    ]
                }
            }
        }
    }
    merged = openapi_spec._inline_schema(spec, {"$ref": "#/components/schemas/Foo"})
    assert merged["required"] == ["a", "b", "c"]
    assert set(merged["properties"].keys()) == {"a", "b", "c"}


def test_allof_with_readonly_respects_for_write() -> None:
    spec = {
        "components": {
            "schemas": {
                "Base": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "readOnly": True},
                        "name": {"type": "string"},
                    },
                },
                "Child": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Base"},
                        {
                            "type": "object",
                            "properties": {
                                "createDate": {"type": "string", "readOnly": True},
                                "description": {"type": "string"},
                            },
                        },
                    ]
                },
            }
        }
    }
    # When resolving for a write, readOnly fields are stripped from both pieces.
    merged = openapi_spec._inline_schema(
        spec, {"$ref": "#/components/schemas/Child"}, for_write=True
    )
    assert set(merged["properties"].keys()) == {"name", "description"}


def test_oneof_and_anyof_are_not_merged() -> None:
    # oneOf/anyOf represent alternatives, not composition — keep them as a list.
    spec = {
        "components": {
            "schemas": {
                "Either": {
                    "oneOf": [
                        {"type": "object", "properties": {"variant": {"const": "a"}}},
                        {"type": "object", "properties": {"variant": {"const": "b"}}},
                    ]
                }
            }
        }
    }
    result = openapi_spec._inline_schema(spec, {"$ref": "#/components/schemas/Either"})
    assert "oneOf" in result
    assert len(result["oneOf"]) == 2


def test_example_body_merges_base_properties_with_first_oneof_alternative() -> None:
    # This mirrors Atlas's hardware-spec pattern: base carries diskSizeGB,
    # and the oneOf adds provider-specific fields like instanceSize + nodeCount.
    # Without merging, the LLM never sees those required fields and the
    # create request fails with MISSING_ATTRIBUTE.
    schema = {
        "type": "object",
        "properties": {
            "diskSizeGB": {"type": "integer"},
        },
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "instanceSize": {"type": "string", "enum": ["M10", "M20", "M30"]},
                    "nodeCount": {"type": "integer"},
                    "ebsVolumeType": {"type": "string", "enum": ["STANDARD", "PROVISIONED"]},
                },
            },
            {
                "type": "object",
                "properties": {
                    "instanceSize": {"type": "string"},
                    "nodeCount": {"type": "integer"},
                },
            },
        ],
    }
    example = openapi_spec._example_from_schema(schema)
    # Base fields AND first-alternative fields both present.
    assert "diskSizeGB" in example
    assert "instanceSize" in example
    assert "nodeCount" in example
    assert example["instanceSize"] == "M10"  # first enum


def test_example_body_falls_back_to_first_oneof_when_no_base() -> None:
    schema = {
        "oneOf": [
            {
                "type": "object",
                "properties": {"a": {"type": "string", "enum": ["x"]}},
            },
            {"type": "object", "properties": {"b": {"type": "integer"}}},
        ],
    }
    example = openapi_spec._example_from_schema(schema)
    assert example == {"a": "x"}


def test_example_body_emits_all_properties_when_required_is_missing() -> None:
    # Fallback for Atlas's reality: `required` is omitted but the API still
    # enforces fields at runtime.
    schema = {
        "type": "object",
        "properties": {
            "priority": {"type": "integer"},
            "providerName": {"type": "string", "enum": ["AWS"]},
        },
    }
    example = openapi_spec._example_from_schema(schema)
    assert set(example.keys()) == {"priority", "providerName"}


def test_example_body_prefers_explicit_example_over_default() -> None:
    schema = {
        "type": "object",
        "required": ["foo"],
        "properties": {
            "foo": {"type": "string", "default": "def", "example": "ex"},
        },
    }
    assert openapi_spec._example_from_schema(schema) == {"foo": "ex"}
