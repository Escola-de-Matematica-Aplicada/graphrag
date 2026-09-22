# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""A module containing snapshot_graphml method definition."""

import networkx as nx
import pandas as pd
from graphrag_storage import Storage


async def snapshot_graphml(
    edges: pd.DataFrame,
    name: str,
    storage: Storage,
) -> None:
    """Take a entire snapshot of a graph to standard graphml format."""
    # Include every optional text attribute that actually exists on this
    # edge frame, instead of hardcoding just "weight" -- `description` was
    # always available here but silently dropped, and `label` is optional
    # depending on whether the extraction prompt requested it.
    edge_attrs: list[str | int] = [
        attr for attr in ("weight", "label", "description") if attr in edges.columns
    ]
    # GraphML does not support None/NaN as a data value (only "weight" is
    # guaranteed non-null); blank out missing text fields instead of failing
    # the whole snapshot on sparse/partial extractions.
    text_attrs = [attr for attr in edge_attrs if attr != "weight"]
    if text_attrs:
        edges = edges.copy()
        edges[text_attrs] = edges[text_attrs].fillna("")
    graph = nx.from_pandas_edgelist(edges, edge_attr=edge_attrs)
    graphml = "\n".join(nx.generate_graphml(graph))
    await storage.set(name + ".graphml", graphml)
