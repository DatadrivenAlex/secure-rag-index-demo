# Secure RAG Index Demo

A local Python/FastAPI reference implementation for one difficult part of enterprise retrieval: filter document chunks by tenant and role before returning results. It uses SQLite FTS5 keyword search, transactional document versioning and citations. It does not call an LLM, create embeddings or provide production authentication.

## Run locally

```bash
python -m pip install '.[dev]'
uvicorn secure_index.api:app --reload
```

```bash
# Ingest a synthetic document. An empty roles array allows every role within the tenant.
curl -X PUT http://localhost:8000/v1/documents/ops-guide \
  -H 'Content-Type: application/json' -H 'X-Tenant-ID: demo' \
  -d '{"title":"Ops guide","text":"Rotate gateway API keys monthly.","roles":["ops"],"expected_version":0}'

# Retrieve the matching chunk, with document ID, version, position and FTS5 rank.
curl 'http://localhost:8000/v1/search?q=gateway' \
  -H 'X-Tenant-ID: demo' -H 'X-Roles: ops'
```

Open `http://localhost:8000/docs` for the OpenAPI interface. To update a document, send its current `expected_version`; stale updates return 409. Delete using `DELETE /v1/documents/ops-guide?expected_version=1`. Search requires matching at least one allowed role; public-within-tenant documents have an empty roles array.

## Design

```text
document --> word chunks (80 words, 15-word overlap) --> SQLite + FTS5
search   --> safe tokenization --> SQL tenant and role filter --> BM25 ranking --> top k chunks
```

Replacement and index updates are one transaction under `BEGIN IMMEDIATE`. Queries filter access before the SQL `LIMIT`, so inaccessible chunks cannot displace accessible results. Citations are document IDs, version numbers and zero-based chunk positions. Search is lexical OR across at most 16 query terms; it uses FTS5 BM25 and does not perform semantic search. FTS corpus statistics are shared across tenants and can affect ranking and scores; strict isolation would require separate indexes per tenant.

## Important limitations

- Headers are simulated claims and can be forged. There is no authentication, authorization for writers, encryption, audit log, network isolation or production security boundary. Run locally with synthetic data only. An actual deployment must verify the caller's identity and authorize reads and writes server-side.
- Full text is stored in SQLite. It has no document-level deletion policy or backups; database files must be protected. No LLM, vector index, external data connectors or hosted deployment are included.
- Version numbers prevent concurrent stale updates but reset after deletion and recreation. There is no history, tombstone or rollback to prior versions.
- BM25 scores depend on global FTS statistics, potentially revealing aggregate information; do not expose them in an untrusted multi-tenant system. Search quality has not been benchmarked on real-world data.

## Checks

```bash
ruff check .
pytest -q
docker build -t secure-rag-index-demo .
docker run --rm -p 8000:8000 secure-rag-index-demo
```

CI runs lint and tests on Python 3.11/3.12. The tests cover tenant and role filtering, filtering before limit, concurrent version conflicts, replacement/deletion and query escaping.
