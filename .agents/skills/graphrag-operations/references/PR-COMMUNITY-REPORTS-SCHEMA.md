# PR candidate: stop requiring a nested-schema `response_format` in `create_community_reports` (`graphrag`, upstream microsoft/graphrag)

Conteúdo em inglês de propósito — é material pronto para virar a descrição
de um PR no repositório upstream (`github.com/microsoft/graphrag`). O diff em
`patches/06-community_reports_extractor.py.diff` usa o path relativo à raiz
do monorepo atual (`packages/graphrag/graphrag/...`, mesmo layout do upstream
desde a reestruturação em uv workspace) — **verificado em 2026-09-22 aplicando
e revertendo com `git apply`/`git apply -R` neste fork**
(Escola-de-Matematica-Aplicada/graphrag, sincronizado com `upstream/main`
além deste patch), e com `tests/verbs/test_create_community_reports.py`
passando após a aplicação (`uv run pytest`).

Achado original portado de `matematica-na-industria/.skills/graphrag/`
(sessão de indexação real contra um Databricks AI Gateway servindo
`gpt-oss-20b`). Ver `PR-EDGE-LABEL.md` para o precedente deste formato de
documento neste fork.

---

## Title

fix(index): stop requiring a nested-schema `response_format` in `create_community_reports`

## Summary

`CommunityReportsExtractor.__call__` passes `response_format=CommunityReportResponse`
to `LLMCompletion.completion_async`, where `CommunityReportResponse.findings` is
typed as `list[FindingModel]` — a nested Pydantic `BaseModel`.

Pydantic's `model_json_schema()` (and litellm's `type_to_response_format_param`,
which builds the actual `{"type": "json_schema", ...}` wire payload from it)
always factors a nested `BaseModel` out into a top-level `$defs` entry
referenced via `$ref`. There is no built-in option to force full inlining.
**Confirmed still true against this fork's pinned `pydantic`** — re-ran
`CommunityReportResponse.model_json_schema()` directly and it still emits a
`$defs`/`$ref` pair for `FindingModel`.

A number of OpenAI-"strict"-mode-compatible guided-JSON backends only
implement the flat subset of JSON Schema and reject any schema containing
`$defs`/`$ref` outright. Concretely, this was hit against a Databricks AI
Gateway serving an open-weights model (`gpt-oss-20b`):

```
litellm.BadRequestError: DatabricksException - {"error_code":"BAD_REQUEST","message":"Invalid JSON schema - /$defs/FindingModel\n"}
```

The same class of guided-decoding backend (vLLM's guided decoding,
`outlines`, `lm-format-enforcer`-based servers) is known to have the same
restriction, so this isn't Databricks-specific.

**Impact**: every single call to `create_community_reports` fails with this
error on any such backend. `summarize_communities` catches the exception
per-community (`No report found for community: N`), so the workflow doesn't
crash immediately and indexing keeps going with a misleading `Pipeline
complete` — the run only reports the failure at the very end, when
`finalize_community_reports` tries to `pandas.merge` the (empty) reports
frame on a `community` column that was never produced, and raises
`KeyError: 'community'`.

## Repro

Direct call against the same endpoint/model, isolating the variable:

```python
class Finding(BaseModel):
    summary: str
    explanation: str

class FlatReport(BaseModel):
    title: str
    summary: str
    rating: float

class NestedReport(BaseModel):
    title: str
    summary: str
    findings: list[Finding]
    rating: float

litellm.completion(..., response_format=FlatReport)    # succeeds
litellm.completion(..., response_format=NestedReport)   # BadRequestError: Invalid JSON schema - /$defs/Finding
```

Substituting an open `dict`/`dict[str, str]` for `Finding` avoids the
`$defs` error but trips a separate rule on the same backend
(`"additionalProperties" keyword must be False or not specified`) — i.e.
this class of backend wants either a `$ref`-free explicit object schema
with `additionalProperties: false`, or no schema constraint at all.

## Fix

Since the prompt already asks for the JSON shape in prose (it always has),
this PR stops relying on server-side structured-output enforcement for this
one call and instead parses + validates the response text on the client:

- Drop `response_format=CommunityReportResponse` from the
  `completion_async(...)` call in `CommunityReportsExtractor.__call__`.
- Add a small `_parse_json_response(content: str) -> dict` helper that
  `json.loads`s the raw text, tolerating a wrapping ` ```json ` fence and
  leading/trailing prose (some models add these despite being asked not
  to) by falling back to slicing between the first `{` and last `}`.
- Construct `CommunityReportResponse(**_parse_json_response(response.content))`
  directly — pydantic still validates the shape and coerces nested dicts
  into `FindingModel` instances, so `_get_text_output`'s attribute access
  (`report.title`, `f.summary`, ...) is unaffected.

No public API changes: `CommunityReportsResult`, `CommunityReportResponse`,
and `FindingModel` are unchanged, so nothing downstream of
`create_community_reports` is affected.

### Alternative considered

Inlining the nested schema (a custom `pydantic.json_schema.GenerateJsonSchema`
subclass that expands `$ref`s instead of collecting them into `$defs`) would
keep server-side schema enforcement for backends that support flat schemas
strictly. Dropping `response_format` instead was chosen because it's a
smaller, more universally compatible change and the prompt-based JSON
instructions were already sufficient in testing (see below) — but a
maintainer may prefer the inlining approach for the stronger guarantee it
gives on backends that *do* support strict flat-schema decoding.

## Testing

- Ran a full `graphrag index` against a real ~1.1MB / 32-document corpus with
  this fix, using the Databricks AI Gateway backend described above:
  - Before the fix: 0 / 357 communities got a report (100% failure, workflow
    ends in a misleading "Pipeline complete" with a `KeyError: 'community'`
    buried in the log).
  - After the fix: 350 / 357 communities got a report (98%); the remaining
    ~2% failed for unrelated, expected reasons (a transient rate limit and one
    case of genuinely malformed JSON from the model), not the schema bug.
- Ported into this fork's monorepo layout and verified
  `tests/verbs/test_create_community_reports.py` passes (`MockLLMCompletion`
  returns the mock JSON directly as `response.content`, independent of
  `response_format`, so the mocked path exercises `_parse_json_response`
  unchanged).

## Checklist

- [ ] Added/updated tests for `CommunityReportsExtractor` (none existed for
      the `response_format` path at the time of writing — a unit test
      mocking `completion_async` to return fenced/unfenced JSON would be a
      good addition)
- [x] No public API / return type changes
- [x] Verified against a real (non-mocked) OpenAI-compatible endpoint
- [x] Existing mock-backed workflow test passes after the change

## Files changed

| # | File | What |
|---|---|---|
| 1 | `packages/graphrag/graphrag/index/operations/summarize_communities/community_reports_extractor.py` | Drop `response_format`; parse/validate JSON client-side via `_parse_json_response` |

Full diff: `patches/06-community_reports_extractor.py.diff`.

## How to apply

```bash
git apply .agents/skills/graphrag-operations/references/patches/06-community_reports_extractor.py.diff
```

from a `graphrag` monorepo checkout (path is relative to the repo root,
`packages/graphrag/graphrag/...`).
