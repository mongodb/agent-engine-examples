---
name: db-patterns
description: MongoDB anti-patterns causing correctness bugs, unbounded resource consumption, race conditions, or data-integrity violations. Covers query patterns, index coverage, document growth, and pagination. Use when reviews touch database access code.
---

# Database & Query Patterns

Flag database-layer bugs that degrade performance, corrupt data, or race under concurrent writers. Cite `REVIEW-RULE-ID-DB-*` tokens in findings.

## MongoDB query patterns

### REVIEW-RULE-ID-DB-UNBOUNDED-FIND
`collection.Find()` / `collection.find()` with no `.Limit()` / `limit=` on a collection that can grow — unbounded result set.

### REVIEW-RULE-ID-DB-N-PLUS-ONE
Loop body containing `collection.FindOne()` / `collection.find_one()` / `collection.Find()` — N+1 pattern; fold into a single `$in` query or aggregation pipeline.

### REVIEW-RULE-ID-DB-LOOKUP-UNINDEXED
`$lookup` stage joining on a field that has no index on the `from` collection — full collection scan on every document.

### REVIEW-RULE-ID-DB-READ-MODIFY-WRITE
Find + in-memory modify + replace/update sequence (`findOne` → edit → `replaceOne`) without `findOneAndUpdate` / `find_one_and_update` or a session transaction — race condition between concurrent writers.

### REVIEW-RULE-ID-DB-MULTI-DOC-NO-TXN
Two or more writes (inserts/updates/deletes across documents or collections) that MUST succeed together, without a session transaction — partial-failure corruption.

### REVIEW-RULE-ID-DB-AGG-NO-LIMIT
Aggregation pipeline with no `$limit` stage on a large collection.

### REVIEW-RULE-ID-DB-SORT-UNINDEXED
`$sort` on a field that has no index — triggers an in-memory sort (visible as `SORT` in `.explain()`).

### REVIEW-RULE-ID-DB-CURSOR-NOT-CLOSED
Cursor iterated with `.Next()` / manual iteration, missing close on early-return paths. Go: `defer cursor.Close(ctx)`. Python: use cursor as a context manager or `finally: cursor.close()`.

## Index coverage

### REVIEW-RULE-ID-DB-NO-INDEX
Query/update/delete filter on a field that has no visible index (no matching entry in migrations, `createIndex` calls, or schema definitions) — every operation is a collection scan.

### REVIEW-RULE-ID-DB-COMPOUND-INDEX
Filter on `{tenantID, status}` where only a single-field index on `tenantID` exists — the compound filter gets no selectivity benefit from the single-field index.

### REVIEW-RULE-ID-DB-SORT-NOT-COVERED
Sort field not covered by the query's index — in-memory sort, slow on large result sets.

## Document growth

### REVIEW-RULE-ID-DB-UNBOUNDED-PUSH
Array field appended to via `$push` without `$slice` or `$each`+`$slice` cap — array grows unbounded; document risks the 16MB BSON limit.

### REVIEW-RULE-ID-DB-UNBOUNDED-EMBEDDED
Embedded sub-document or array populated by user activity (audit logs, messages, events) with no size guard or archival strategy.

## Pagination

### REVIEW-RULE-ID-DB-NO-PAGINATION
List/search endpoint that can return all matching documents with no `$limit`.

### REVIEW-RULE-ID-DB-SKIP-OFFSET
`skip = page * pageSize` or `offset`-based pagination on a large/growing collection. Scans and discards documents on every page; degrades at high pages; inconsistent under concurrent writes. Recommend cursor-based pagination: filter on `{ _id: { $gt: lastSeenId } }` with `$sort` + `$limit`.

## What NOT to flag

- Small collections with known hard bounds (e.g. enum tables, feature-flag definitions)
- Tests that do full scans deliberately
- Pre-existing anti-patterns on unchanged lines

## Output

Every finding MUST cite a specific `REVIEW-RULE-ID-DB-*` token from this skill.
