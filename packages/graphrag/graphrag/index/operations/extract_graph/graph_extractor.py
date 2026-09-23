# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""Graph extraction helpers that return tabular data."""

import logging
import re
import traceback
from typing import TYPE_CHECKING, Any

import pandas as pd
from graphrag_llm.utils import (
    CompletionMessagesBuilder,
)

from graphrag.index.typing.error_handler import ErrorHandlerFn
from graphrag.index.utils.string import clean_str
from graphrag.prompts.index.extract_graph import (
    CONTINUE_PROMPT,
    LOOP_PROMPT,
)

if TYPE_CHECKING:
    from graphrag_llm.completion import LLMCompletion
    from graphrag_llm.types import LLMCompletionResponse

INPUT_TEXT_KEY = "input_text"
RECORD_DELIMITER_KEY = "record_delimiter"
COMPLETION_DELIMITER_KEY = "completion_delimiter"
ENTITY_TYPES_KEY = "entity_types"
TUPLE_DELIMITER = "<|>"
RECORD_DELIMITER = "##"
COMPLETION_DELIMITER = "<|COMPLETE|>"

logger = logging.getLogger(__name__)


class GraphExtractor:
    """Unipartite graph extractor class definition."""

    _model: "LLMCompletion"
    _extraction_prompt: str
    _max_gleanings: int
    _on_error: ErrorHandlerFn

    def __init__(
        self,
        model: "LLMCompletion",
        prompt: str,
        max_gleanings: int,
        on_error: ErrorHandlerFn | None = None,
    ):
        """Init method definition."""
        self._model = model
        self._extraction_prompt = prompt
        self._max_gleanings = max_gleanings
        self._on_error = on_error or (lambda _e, _s, _d: None)

    async def __call__(
        self, text: str, entity_types: list[str], source_id: str
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Extract entities and relationships from the supplied text."""
        try:
            # Invoke the entity extraction
            result = await self._process_document(text, entity_types)
        except Exception as e:  # pragma: no cover - defensive logging
            logger.exception("error extracting graph")
            self._on_error(
                e,
                traceback.format_exc(),
                {
                    "source_id": source_id,
                    "text": text,
                },
            )
            return _empty_entities_df(), _empty_relationships_df()

        return self._process_result(
            result,
            source_id,
            TUPLE_DELIMITER,
            RECORD_DELIMITER,
        )

    async def _process_document(self, text: str, entity_types: list[str]) -> str:
        messages_builder = CompletionMessagesBuilder().add_user_message(
            self._extraction_prompt.format(**{
                INPUT_TEXT_KEY: text,
                ENTITY_TYPES_KEY: ",".join(entity_types),
            })
        )

        response: LLMCompletionResponse = await self._model.completion_async(
            messages=messages_builder.build(),
        )  # type: ignore
        results = response.content
        messages_builder.add_assistant_message(results)

        # if gleanings are specified, enter a loop to extract more entities
        # there are two exit criteria: (a) we hit the configured max, (b) the model says there are no more entities
        if self._max_gleanings > 0:
            for i in range(self._max_gleanings):
                messages_builder.add_user_message(CONTINUE_PROMPT)
                response: LLMCompletionResponse = await self._model.completion_async(
                    messages=messages_builder.build(),
                )  # type: ignore
                response_text = response.content
                messages_builder.add_assistant_message(response_text)
                results += response_text

                # if this is the final glean, don't bother updating the continuation flag
                if i >= self._max_gleanings - 1:
                    break

                messages_builder.add_user_message(LOOP_PROMPT)
                response: LLMCompletionResponse = await self._model.completion_async(
                    messages=messages_builder.build(),
                )  # type: ignore
                if response.content != "Y":
                    break

        return results

    def _process_result(
        self,
        result: str,
        source_id: str,
        tuple_delimiter: str,
        record_delimiter: str,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Parse the result string into entity and relationship data frames."""
        entities: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []

        # `result` is the raw text of possibly several concatenated completions
        # (one per gleaning round), joined with no separator in `_process_document`.
        # Once the model emits COMPLETION_DELIMITER it considers itself done; any
        # text after that point (e.g. a later gleaning round's reply, or the model
        # rambling past its own "done" signal) has no record_delimiter separating
        # it from the last real record, so it would otherwise merge into whatever
        # field reads to the end of that record (weight) or a fixed index past the
        # emitted fields (the optional relationship label). Cut it off first.
        result = result.split(COMPLETION_DELIMITER, 1)[0]
        records = [r.strip() for r in result.split(record_delimiter)]

        for raw_record in records:
            record = re.sub(r"^\(|\)$", "", raw_record.strip())
            if not record or record == COMPLETION_DELIMITER:
                continue

            record_attributes = record.split(tuple_delimiter)
            record_type = record_attributes[0]

            if record_type == '"entity"' and len(record_attributes) >= 4:
                entity_name = clean_str(record_attributes[1].upper())
                entity_type = clean_str(record_attributes[2].upper())
                entity_description = clean_str(record_attributes[3])
                entities.append({
                    "title": entity_name,
                    "type": entity_type,
                    "description": entity_description,
                    "source_id": source_id,
                })

            if record_type == '"relationship"' and len(record_attributes) >= 5:
                source = clean_str(record_attributes[1].upper())
                target = clean_str(record_attributes[2].upper())
                edge_description = clean_str(record_attributes[3])
                try:
                    weight = float(record_attributes[-1])
                except ValueError:
                    weight = 1.0
                # Optional 6th tuple field: a short (~3-word) relationship label.
                # It sits between `description` (fixed index 3, never moves)
                # and `weight` (always read via [-1], so it stays robust to
                # this extra field). Prompts that only emit the original
                # 5-field tuple keep working unchanged (label = "").
                # Models don't always honor the "three words" instruction
                # exactly, so cap it defensively instead of trusting the
                # prompt alone -- this is the only guard against a long
                # label swamping the graphml edge display.
                edge_label = (
                    " ".join(clean_str(record_attributes[4]).split()[:6])
                    if len(record_attributes) >= 6
                    else ""
                )

                relationships.append({
                    "source": source,
                    "target": target,
                    "description": edge_description,
                    "source_id": source_id,
                    "weight": weight,
                    "label": edge_label,
                })

        entities_df = pd.DataFrame(entities) if entities else _empty_entities_df()
        relationships_df = (
            pd.DataFrame(relationships) if relationships else _empty_relationships_df()
        )

        return entities_df, relationships_df


def _empty_entities_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["title", "type", "description", "source_id"])


def _empty_relationships_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["source", "target", "weight", "description", "source_id", "label"]
    )
