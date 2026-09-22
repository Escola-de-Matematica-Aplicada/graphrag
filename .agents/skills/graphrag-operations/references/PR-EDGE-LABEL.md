# PR candidate: short relationship label field (`graphrag`, upstream microsoft/graphrag)

Conteúdo em inglês de propósito — é material pronto para virar a descrição
de um PR no repositório upstream (`github.com/microsoft/graphrag`). Os
diffs reais (gerados a partir de um `graphrag==3.1.2` pristino vs. o mesmo
pacote depois de rodar `../scripts/apply-edge-label-patch.py`) estão em
`patches/01-schemas.py.diff` .. `patches/04-snapshot_graphml.py.diff`.
Reproduzido/validado em produção real: ver
`edge-label-patch.md` (contexto completo) e a seção "Validação recomendada"
lá para o roteiro de teste.

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
- **The trickiest bug, and the reason this is 4 files, not 1**:
  `_merge_relationships()` (in `extract_graph.py`) merges per-chunk
  relationship frames with a **hardcoded column list** in
  `.groupby().agg(...)`. Any column not named there is silently dropped —
  no error, no warning — even if the parser and schema are both patched
  correctly. This is the actual root cause if you find `label` reaching the
  parser but not the final parquet.
- **GraphML does not accept `None`/NaN as a data value.** `label`/
  `description` can be null on relationships that predate this feature or
  came from a non-LLM extractor (`extract_graph_nlp` never populates either
  field). The snapshot step fills missing text attributes with `""` before
  handing the frame to `networkx`; `weight` (always numeric, never null) is
  left untouched.

## Files changed

| # | File | What |
|---|---|---|
| 1 | `graphrag/data_model/schemas.py` | New `EDGE_LABEL` constant, added to `RELATIONSHIPS_FINAL_COLUMNS` |
| 2 | `graphrag/index/operations/extract_graph/graph_extractor.py` | Parser reads the optional 6th tuple field; `_empty_relationships_df()` includes the column |
| 3 | `graphrag/index/operations/extract_graph/extract_graph.py` | `_merge_relationships()` preserves `label` through the groupby/agg instead of silently dropping it |
| 4 | `graphrag/index/operations/snapshot_graphml.py` | `.graphml` export includes `label`/`description` (not just `weight`), with NaN-safe handling |

Full diffs: `patches/01-schemas.py.diff`, `patches/02-graph_extractor.py.diff`,
`patches/03-extract_graph.py.diff`, `patches/04-snapshot_graphml.py.diff`.

## How to apply

```bash
python3 apply-edge-label-patch.py   # ../scripts/apply-edge-label-patch.py — idempotent
```

or apply the 4 diff files directly with `patch -p1` / `git apply` from a
`graphrag` checkout.

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
