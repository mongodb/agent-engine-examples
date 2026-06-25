---
name: config-docs
description: Configuration documentation — environment variables, YAML/JSON config files, feature flags, defaults. Detects undocumented or stale configuration changes.
---

# Configuration Documentation

Identify configuration documentation that has drifted from the codebase and draft updated content. Cite `DOCS-RULE-ID-CFG-*` tokens in findings.

## Scope

You are responsible for documentation of environment variables, configuration files (YAML, JSON, TOML), feature flags, and default values. Focus on changes to config structs, env var reads, and config file schemas.

## Rules

### DOCS-RULE-ID-CFG-ENV-VAR
A new environment variable was introduced via `os.Getenv()`, `os.environ.get()`, `import.meta.env.*`, or similar — but no documentation entry exists for it.

**Check for:**
- New `os.environ.get("NEW_VAR")` or `os.Getenv("NEW_VAR")` calls
- New entries in `.env.example` or `env.example` files without doc updates
- New config struct fields that map to env vars

### DOCS-RULE-ID-CFG-DEFAULT
A default value for a configuration option changed in the code but the documentation still shows the old default.

**Check for:**
- Changed fallback values in `os.environ.get("VAR", "old_default")`
- Modified default values in config struct definitions
- Changed default ports, timeouts, or feature flag states

### DOCS-RULE-ID-CFG-REMOVED
A configuration option was removed from the code but is still documented. Stale config documentation leads users to set variables that have no effect.

**Check for:**
- Env var reads that were deleted
- Config struct fields that were removed
- Feature flags that were consolidated or removed

### DOCS-RULE-ID-CFG-YAML
A YAML, JSON, or TOML config schema changed — new fields added, fields removed, field types changed, or nesting restructured — without a corresponding doc update.

**Check for:**
- New keys in `agent.yaml`, `config.yaml`, or similar config files
- Changed field names or nesting structure
- Modified validation rules for config values

## Output format

For each finding, provide:

```
**File:** <path to the doc file that needs updating>
**Rule:** DOCS-RULE-ID-CFG-<name>
**What changed:** <brief description of the config change>
**Current text:** <the stale section from the doc, or "N/A — no existing entry">
**Suggested update:** <the corrected or new text>
```

Separate multiple findings with `---` on its own line.

If no configuration doc updates are needed, return exactly: `No findings.`
