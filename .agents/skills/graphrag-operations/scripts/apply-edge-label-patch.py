#!/usr/bin/env python3
"""Reaplica o patch de 5 arquivos (label curta nas arestas) no pacote
`graphrag` instalado em site-packages.

Por que este script existe: o patch documentado em
`../references/edge-label-patch.md` foi feito editando o pacote instalado
(fora de /workspaces). Nesta imagem docker, tudo fora de /workspaces vive no
filesystem 'overlay' efemero do container -- um restart apaga o patch e o
graphrag volta ao comportamento padrao (sem coluna `label` em
relationships.parquet, `.graphml` so com `weight`). Rode este script depois
de qualquer restart de container, antes de indexar, para garantir que o
campo `label` sobreviva ate o `relationships.parquet`/`graph.graphml` final.

Idempotente: cada patch checa uma marca antes de aplicar; rodar de novo em
um pacote ja patchado nao faz nada (imprime "ja aplicado").

Sequencia de patches (nesta ordem -- ordem importa: os passos 2-5 dependem
conceitualmente do nome de campo introduzido no passo 1, mesmo que o script
aplique por texto literal e nao por import):
  1. data_model/schemas.py            -- add EDGE_LABEL field constant
  2. extract_graph/graph_extractor.py -- parse the optional 6th tuple field
  3. extract_graph/extract_graph.py   -- preserve `label` through the merge/groupby
  4. snapshot_graphml.py              -- include `label`/`description` in .graphml export
  5. index/update/relationships.py    -- same fix as #3, but for the incremental-update
                                          path's own independent hardcoded agg() column list
                                          (only relevant if incremental indexing is used)

Os comentarios dentro dos blocos `new=` abaixo estao em ingles de proposito:
sao o texto que efetivamente entra no arquivo do pacote (destino de um PR
upstream em github.com/microsoft/graphrag, um projeto em ingles).
"""

import sys
from pathlib import Path

try:
    import graphrag
except ImportError:
    print("ERRO: pacote graphrag nao encontrado neste Python.", file=sys.stderr)
    sys.exit(1)

ROOT = Path(graphrag.__file__).parent


def patch_file(path: Path, marker: str, old: str, new: str, label: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        print(f"[skip] {label}: ja aplicado ({path})")
        return False
    if old not in text:
        print(f"[FALHOU] {label}: trecho esperado nao encontrado em {path}")
        print(
            "  Verifique se a versao do graphrag mudou a estrutura interna do arquivo."
        )
        return False
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    print(f"[ok] {label}: aplicado em {path}")
    return True


def main() -> None:
    changed = False

    # --- Patch 1/4: data_model/schemas.py -- new constant + column in the final schema ---
    schemas_path = ROOT / "data_model" / "schemas.py"
    changed |= patch_file(
        schemas_path,
        marker='EDGE_LABEL = "label"',
        old='EDGE_WEIGHT = "weight"',
        new=(
            'EDGE_WEIGHT = "weight"\n'
            "# Short (~3 word) verb-phrase label summarizing the relationship, distinct\n"
            "# from the free-text `description` field. Optional: extraction prompts that\n"
            "# do not request it simply leave every row empty.\n"
            'EDGE_LABEL = "label"'
        ),
        label="schemas.py: constante EDGE_LABEL",
    )
    changed |= patch_file(
        schemas_path,
        marker="EDGE_LABEL,\n    EDGE_WEIGHT,",
        old="    DESCRIPTION,\n    EDGE_WEIGHT,\n    EDGE_DEGREE,\n    TEXT_UNIT_IDS,\n]\n\nCOMMUNITIES_FINAL_COLUMNS",
        new="    DESCRIPTION,\n    EDGE_LABEL,\n    EDGE_WEIGHT,\n    EDGE_DEGREE,\n    TEXT_UNIT_IDS,\n]\n\nCOMMUNITIES_FINAL_COLUMNS",
        label="schemas.py: EDGE_LABEL em RELATIONSHIPS_FINAL_COLUMNS",
    )

    # --- Patch 2/4: extract_graph/graph_extractor.py -- parser accepts an optional 6th field ---
    extractor_path = (
        ROOT / "index" / "operations" / "extract_graph" / "graph_extractor.py"
    )
    changed |= patch_file(
        extractor_path,
        marker="clean_str(record_attributes[4])",
        old=(
            "                try:\n"
            "                    weight = float(record_attributes[-1])\n"
            "                except ValueError:\n"
            "                    weight = 1.0\n"
            "\n"
            "                relationships.append({\n"
            '                    "source": source,\n'
            '                    "target": target,\n'
            '                    "description": edge_description,\n'
            '                    "source_id": source_id,\n'
            '                    "weight": weight,\n'
            "                })"
        ),
        new=(
            "                try:\n"
            "                    weight = float(record_attributes[-1])\n"
            "                except ValueError:\n"
            "                    weight = 1.0\n"
            "                # Optional 6th tuple field: a short relationship label.\n"
            "                # It sits between `description` (fixed index 3, never moves)\n"
            "                # and `weight` (always read via [-1], so it stays robust to\n"
            "                # this extra field). Prompts that only emit the original\n"
            '                # 5-field tuple keep working unchanged (label = "").\n'
            "                edge_label = (\n"
            "                    clean_str(record_attributes[4])\n"
            "                    if len(record_attributes) >= 6\n"
            '                    else ""\n'
            "                )\n"
            "\n"
            "                relationships.append({\n"
            '                    "source": source,\n'
            '                    "target": target,\n'
            '                    "description": edge_description,\n'
            '                    "source_id": source_id,\n'
            '                    "weight": weight,\n'
            '                    "label": edge_label,\n'
            "                })"
        ),
        label="graph_extractor.py: parser aceita label (6o campo)",
    )
    changed |= patch_file(
        extractor_path,
        marker='columns=["source", "target", "weight", "description", "source_id", "label"]',
        old='columns=["source", "target", "weight", "description", "source_id"]',
        new='columns=["source", "target", "weight", "description", "source_id", "label"]',
        label="graph_extractor.py: _empty_relationships_df com label",
    )

    # --- Patch 3/4: extract_graph/extract_graph.py -- _merge_relationships keeps `label` ---
    merge_path = ROOT / "index" / "operations" / "extract_graph" / "extract_graph.py"
    changed |= patch_file(
        merge_path,
        marker='if "label" in all_relationships.columns',
        old=(
            "def _merge_relationships(relationship_dfs) -> pd.DataFrame:\n"
            "    all_relationships = pd.concat(relationship_dfs, ignore_index=False)\n"
            "    return (\n"
            "        all_relationships\n"
            '        .groupby(["source", "target"], sort=False)\n'
            "        .agg(\n"
            '            description=("description", list),\n'
            '            text_unit_ids=("source_id", list),\n'
            '            weight=("weight", "sum"),\n'
            "        )\n"
            "        .reset_index()\n"
            "    )"
        ),
        new=(
            "def _merge_relationships(relationship_dfs) -> pd.DataFrame:\n"
            "    all_relationships = pd.concat(relationship_dfs, ignore_index=False)\n"
            "    # Base aggregation always present. Any extra optional per-relationship\n"
            "    # column (currently only `label`) must be added conditionally here --\n"
            "    # `.agg()` silently drops any dataframe column that is not named in this\n"
            "    # dict, with no warning, so this is the one place a new relationship\n"
            "    # field can vanish without a trace.\n"
            "    agg = {\n"
            '        "description": ("description", list),\n'
            '        "text_unit_ids": ("source_id", list),\n'
            '        "weight": ("weight", "sum"),\n'
            "    }\n"
            '    if "label" in all_relationships.columns:\n'
            "        # Same label repeats across duplicate (source, target) rows coming\n"
            "        # from different text chunks; keep the first non-empty occurrence.\n"
            '        agg["label"] = ("label", "first")\n'
            "    return (\n"
            "        all_relationships\n"
            '        .groupby(["source", "target"], sort=False)\n'
            "        .agg(**agg)\n"
            "        .reset_index()\n"
            "    )"
        ),
        label="extract_graph.py: _merge_relationships preserva label",
    )

    # --- Patch 4/4: snapshot_graphml.py -- include label/description in the .graphml export ---
    snapshot_path = ROOT / "index" / "operations" / "snapshot_graphml.py"
    changed |= patch_file(
        snapshot_path,
        marker='attr for attr in ("weight", "label", "description")',
        old='    graph = nx.from_pandas_edgelist(edges, edge_attr=["weight"])',
        new=(
            "    # Include every optional text attribute that actually exists on this\n"
            '    # edge frame, instead of hardcoding just "weight" -- `description` was\n'
            "    # always available here but silently dropped, and `label` is optional\n"
            "    # depending on whether the extraction prompt requested it.\n"
            "    edge_attrs = [\n"
            '        attr for attr in ("weight", "label", "description") if attr in edges.columns\n'
            "    ]\n"
            '    # GraphML does not support None/NaN as a data value (only "weight" is\n'
            "    # guaranteed non-null); blank out missing text fields instead of failing\n"
            "    # the whole snapshot on sparse/partial extractions.\n"
            '    text_attrs = [attr for attr in edge_attrs if attr != "weight"]\n'
            "    if text_attrs:\n"
            "        edges = edges.copy()\n"
            '        edges[text_attrs] = edges[text_attrs].fillna("")\n'
            "    graph = nx.from_pandas_edgelist(edges, edge_attr=edge_attrs)"
        ),
        label="snapshot_graphml.py: inclui label/description no .graphml",
    )

    # --- Patch 5/5: index/update/relationships.py -- incremental-update path has its own
    # independent hardcoded agg() column list (separate from extract_graph.py's) ---
    update_relationships_path = ROOT / "index" / "update" / "relationships.py"
    changed |= patch_file(
        update_relationships_path,
        marker='if "label" in merged_relationships.columns',
        old=(
            "    # Group by title and resolve conflicts\n"
            "    aggregated = (\n"
            "        merged_relationships\n"
            '        .groupby(["source", "target"])\n'
            "        .agg({\n"
            '            "id": "first",\n'
            '            "human_readable_id": "first",\n'
            '            "description": lambda x: list(x.astype(str)),  # Ensure str\n'
            "            # Concatenate nd.array into a single list\n"
            '            "text_unit_ids": lambda x: list(itertools.chain(*x.tolist())),\n'
            '            "weight": "mean",\n'
            '            "combined_degree": "sum",\n'
            "        })\n"
            "        .reset_index()\n"
            "    )\n"
            "\n"
            "    # Force the result into a DataFrame\n"
            "    final_relationships: pd.DataFrame = pd.DataFrame(aggregated)\n"
        ),
        new=(
            "    # Group by title and resolve conflicts. Base aggregation always present;\n"
            "    # any extra optional per-relationship column (currently only `label`)\n"
            "    # must be added conditionally -- `.agg()` silently drops any dataframe\n"
            "    # column not named here, with no warning.\n"
            "    agg = {\n"
            '        "id": "first",\n'
            '        "human_readable_id": "first",\n'
            '        "description": lambda x: list(x.astype(str)),  # Ensure str\n'
            "        # Concatenate nd.array into a single list\n"
            '        "text_unit_ids": lambda x: list(itertools.chain(*x.tolist())),\n'
            '        "weight": "mean",\n'
            '        "combined_degree": "sum",\n'
            "    }\n"
            '    if "label" in merged_relationships.columns:\n'
            '        agg["label"] = "first"\n'
            "    aggregated = (\n"
            '        merged_relationships.groupby(["source", "target"]).agg(agg).reset_index()\n'
            "    )\n"
            "\n"
            "    # Force the result into a DataFrame\n"
            "    final_relationships: pd.DataFrame = pd.DataFrame(aggregated)\n"
            "\n"
            "    # `label` is optional (absent for indexes built before this field\n"
            "    # existed, or via extractors that never populate it) -- backfill so the\n"
            "    # final column selection below does not KeyError.\n"
            '    if "label" not in final_relationships.columns:\n'
            '        final_relationships["label"] = ""\n'
        ),
        label="relationships.py: incremental-update agg() preserva label",
    )

    print()
    if changed:
        print("Patch aplicado (ou parcialmente reaplicado). Confirme com:")
    else:
        print("Nada a fazer -- pacote ja estava totalmente patchado.")
    print(
        "  python3 -c \"import graphrag.data_model.schemas as s; print('EDGE_LABEL' in dir(s))\""
    )


if __name__ == "__main__":
    main()
