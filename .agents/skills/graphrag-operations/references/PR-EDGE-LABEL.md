# PR candidate: short relationship label field (`graphrag`, upstream microsoft/graphrag)

Conteúdo em inglês de propósito — é material pronto para virar a descrição
de um PR no repositório upstream (`github.com/microsoft/graphrag`). Os
diffs em `patches/01-schemas.py.diff` .. `patches/05-update_relationships.py.diff`
usam paths relativos à raiz do monorepo atual (`packages/graphrag/graphrag/...`,
o mesmo layout do upstream `microsoft/graphrag` desde a reestruturação em uv
workspace) — **verificados em 2026-09-22 aplicando/revertendo com
`git apply`/`git apply -R` neste fork** (Escola-de-Matematica-Aplicada/graphrag,
que está sincronizado com `upstream/main` além deste patch). Uma versão
anterior destes diffs usava paths de instalação `pip` (`graphrag/...` sem o
prefixo `packages/graphrag/`) gerados contra um `graphrag==3.1.2` pristino
instalado via pip — ainda válidos nesse cenário (site-packages), mas não
aplicam direto num checkout do monorepo sem ajustar o path.
Reproduzido/validado em produção real: ver
`edge-label-patch.md` (contexto completo) e a seção "Validação recomendada"
lá para o roteiro de teste.

**Status (2026-09-22)**: não reportado upstream. `microsoft/graphrag` está
em modo de manutenção ("won't be implementing new features, but we are
accepting PRs for bug fixes" — `CONTRIBUTING.md`) e este achado é uma
feature nova, não um bugfix — provavelmente seria rejeitado de cara. PR
direto também está bloqueado (restrito a colaboradores). Ver
`PR-COMMUNITY-REPORTS-SCHEMA.md` para o achado irmão que *foi* reportado
(issue #2571, por se encaixar como bugfix). Branch `upstream-pr/edge-label`
mantida no fork (`origin`) caso a política mude ou surja outro canal.

---

## Summary

Add an optional short relationship label (a ~3-word verb phrase, e.g.
*"joined the party"*, *"opposed reform"*) alongside the existing free-text
`description` field on extracted relationships, and make sure it survives
all the way to `relationships.parquet` and the `.graphml` snapshot.

## Motivation

`extract_graph`'s prompt already lets you customize what the LLM extracts
per relationship, but there is no structured field for a short, scannable
label distinct from the (often long) `description`. Consumers that render
the graph visually (Gephi, yEd, any `.graphml` viewer) currently have
nothing better to show on an edge than the numeric `weight` — the
`description` column exists in `relationships.parquet` but is dropped
entirely by `snapshot_graphml.py`, which only ever included `weight` in
`edge_attr`. This PR:

1. Adds a `label` column to the relationships schema (optional — populated
   only if the extraction prompt asks for it; empty otherwise, fully
   backward compatible).
2. Fixes `snapshot_graphml.py` to include `description` (already present in
   the data, just never exported) and the new `label` in the `.graphml`
   output.

## Design notes

- **Backward compatible tuple format.** The relationship tuple emitted by
  the LLM is `("relationship"<|>source<|>target<|>description<|>strength)`
  (4 fields after the type tag). The new optional label is inserted
  **between** `description` and `strength` — not appended at the end —
  because `description` is read by a fixed index (`record_attributes[3]`)
  while `weight`/`strength` is read via `record_attributes[-1]`, which is
  already robust to extra trailing-adjacent fields. A prompt that still
  emits the original 4-field tuple keeps working unchanged (`label` ends up
  `""`).
- **The trickiest bug, and the reason this is 5 files, not 1**:
  `_merge_relationships()` (in `extract_graph.py`) merges per-chunk
  relationship frames with a **hardcoded column list** in
  `.groupby().agg(...)`. Any column not named there is silently dropped —
  no error, no warning — even if the parser and schema are both patched
  correctly. This is the actual root cause if you find `label` reaching the
  parser but not the final parquet.
- **Same bug, second occurrence, found while porting this patch to a live
  fork**: `index/update/relationships.py::_update_and_merge_relationships`
  (the incremental-indexing path) has its own independent hardcoded
  `.groupby().agg({...})` column list, entirely separate from the one in
  `extract_graph.py`. Adding `EDGE_LABEL` to `RELATIONSHIPS_FINAL_COLUMNS`
  without patching this second call site makes the final
  `.loc[:, RELATIONSHIPS_FINAL_COLUMNS]` selection raise
  `KeyError: "['label'] not in index"` the first time an incremental update
  runs — caught by `tests/unit/indexing/update/test_update_relationships.py`
  (8 failures). Fixed the same way: add `label` to the aggregation
  conditionally, then backfill `""` if still absent before the final column
  selection. This is now file 5 (`patches/05-update_relationships.py.diff`).
- **GraphML does not accept `None`/NaN as a data value.** `label`/
  `description` can be null on relationships that predate this feature or
  came from a non-LLM extractor (`extract_graph_nlp` never populates either
  field). The snapshot step fills missing text attributes with `""` before
  handing the frame to `networkx`; `weight` (always numeric, never null) is
  left untouched.

## Files changed

| # | File | What |
|---|---|---|
| 1 | `packages/graphrag/graphrag/data_model/schemas.py` | New `EDGE_LABEL` constant, added to `RELATIONSHIPS_FINAL_COLUMNS` |
| 2 | `packages/graphrag/graphrag/index/operations/extract_graph/graph_extractor.py` | Parser reads the optional 6th tuple field; `_empty_relationships_df()` includes the column |
| 3 | `packages/graphrag/graphrag/index/operations/extract_graph/extract_graph.py` | `_merge_relationships()` preserves `label` through the groupby/agg instead of silently dropping it |
| 4 | `packages/graphrag/graphrag/index/operations/snapshot_graphml.py` | `.graphml` export includes `label`/`description` (not just `weight`), with NaN-safe handling |
| 5 | `packages/graphrag/graphrag/index/update/relationships.py` | `_update_and_merge_relationships()` (incremental-update path) preserves `label` the same way; backfills `""` if absent |

Full diffs: `patches/01-schemas.py.diff`, `patches/02-graph_extractor.py.diff`,
`patches/03-extract_graph.py.diff`, `patches/04-snapshot_graphml.py.diff`,
`patches/05-update_relationships.py.diff`.

## How to apply

```bash
python3 apply-edge-label-patch.py   # ../scripts/apply-edge-label-patch.py — idempotent
```

(the script currently reapplies files 1-4 against a pip-installed
`graphrag` in site-packages; it does not yet cover file 5 since that patch
was found later, on the incremental-update path — apply
`patches/05-update_relationships.py.diff` by hand if patching a pip install
that supports incremental indexing) or apply the 5 diff files directly with
`patch -p1` / `git apply` from a `graphrag` monorepo checkout (paths are
relative to the repo root, e.g. `packages/graphrag/graphrag/...`).

## Test evidence (real corpus, not synthetic)

Validated end-to-end (`--method standard`, full pipeline including
`create_community_reports` and `generate_text_embeddings`) on a 20-document
sample of a Brazilian political-biography corpus (DHBB), with
`extract_graph.txt` prompt-tuned to request the label:

- **380/381 relationships (99.7%) produced a non-empty `label`.**
- `.graphml` output now has 3 `<data>` keys per edge (`weight`, `label`,
  `description`) instead of 1.
- Example extracted labels: `"filiou-se ao partido"`, `"era sucessor
  político"`, `"recebeu apoio presidencial"` — all in the language the
  corpus/prompt use (the label instruction is entirely prompt-driven, no
  code change needed to change target language).

No regression observed on the existing 5-field (no-label) tuple format —
tested by running the same pipeline against `extract_graph.txt` prompts
that don't mention `relationship_label` at all (falls back to `label=""`
everywhere, no crash, no schema mismatch).

## Suggested prompt-side addition (not part of this PR's code diff, but needed to actually get labels)

For consumers who prompt-tune their own `extract_graph.txt`, the label
instruction should read (see `edge-label-patch.md` for why `"EXACTLY N
words"` is a bad idea with reasoning models):

```
- relationship_label: a very short label for this relationship, ideally
  three words (never more than four), as a verb phrase summarizing the
  relationship — do not overthink the exact word count, just keep it brief
Format each relationship as ("relationship"<|><source_entity><|><target_entity><|><relationship_description><|><relationship_label><|><relationship_strength>)
```
