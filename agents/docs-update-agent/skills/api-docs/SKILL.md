---
name: api-docs
description: API documentation — REST endpoints, gRPC services, SDK methods, request/response schemas. Detects undocumented or stale API surface changes.
---

# API Documentation

Identify API documentation that has drifted from the codebase and draft updated content. Cite `DOCS-RULE-ID-API-*` tokens in findings.

## Scope

You are responsible for API-related documentation: endpoint references, SDK method docs, request/response schema docs, and API guides. Focus on changes to handler/router files, proto definitions, and public SDK interfaces.

## Rules

### DOCS-RULE-ID-API-ENDPOINT
A new REST or gRPC endpoint was added, or an existing endpoint's path, method, or behavior changed, without corresponding documentation updates.

**Check for:**
- New router/handler registrations without a docs entry
- Changed URL paths or HTTP methods
- Modified authentication or authorization requirements on endpoints

### DOCS-RULE-ID-API-PARAMS
Request parameters, response fields, or schema structures changed but the API documentation still describes the old shape.

**Check for:**
- Added or removed request body fields
- Changed field types or validation rules
- New query parameters or headers
- Modified response envelope structure

### DOCS-RULE-ID-API-EXAMPLES
API usage examples in documentation use patterns that no longer work — deprecated endpoints, removed fields, or changed authentication flows.

**Check for:**
- curl/HTTP examples hitting endpoints that changed
- SDK code examples using old method signatures
- Example responses that no longer match the actual format

### DOCS-RULE-ID-API-ERRORS
Error responses or status codes changed but the API documentation still lists the old error behavior.

**Check for:**
- New error codes or changed HTTP status codes
- Modified error response body format
- New validation error cases not documented

## Output format

For each finding, provide:

```
**File:** <path to the doc file that needs updating>
**Rule:** DOCS-RULE-ID-API-<name>
**What changed:** <brief description of the API change>
**Current text:** <the stale section from the doc>
**Suggested update:** <the corrected text to replace it with>
```

Separate multiple findings with `---` on its own line.

If no API doc updates are needed, return exactly: `No findings.`
