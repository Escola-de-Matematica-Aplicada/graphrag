# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""A module containing 'CommunityReportsResult' and 'CommunityReportsExtractor' models."""

import json
import logging
import re
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from graphrag.index.typing.error_handler import ErrorHandlerFn

if TYPE_CHECKING:
    from graphrag_llm.completion import LLMCompletion

logger = logging.getLogger(__name__)

# these tokens are used in the prompt
INPUT_TEXT_KEY = "input_text"
MAX_LENGTH_KEY = "max_report_length"


class FindingModel(BaseModel):
    """A model for the expected LLM response shape."""

    summary: str = Field(description="The summary of the finding.")
    explanation: str = Field(description="An explanation of the finding.")


class CommunityReportResponse(BaseModel):
    """A model for the expected LLM response shape."""

    title: str = Field(description="The title of the report.")
    summary: str = Field(description="A summary of the report.")
    findings: list[FindingModel] = Field(
        description="A list of findings in the report."
    )
    rating: float = Field(description="The rating of the report.")
    rating_explanation: str = Field(description="An explanation of the rating.")


# Matches a leading/trailing ``` or ```json code fence, so that a model which
# wraps its JSON answer in markdown (despite being asked not to) can still be
# parsed.
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _parse_json_response(content: str) -> dict:
    """Parse a JSON object out of a raw completion string.

    Tolerates markdown code fences and leading/trailing prose that some
    models add around the JSON despite prompt instructions to return JSON
    only.

    Parameters
    ----------
    content : str
        The raw text returned by the completion model.

    Returns
    -------
    dict
        The parsed JSON object.

    Raises
    ------
    json.JSONDecodeError
        If no valid JSON object could be located in `content`.
    """
    text = _JSON_FENCE_RE.sub("", content).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


@dataclass
class CommunityReportsResult:
    """Community reports result class definition."""

    output: str
    structured_output: CommunityReportResponse | None


class CommunityReportsExtractor:
    """Community reports extractor class definition."""

    _model: "LLMCompletion"
    _extraction_prompt: str
    _output_formatter_prompt: str
    _on_error: ErrorHandlerFn
    _max_report_length: int

    def __init__(
        self,
        model: "LLMCompletion",
        extraction_prompt: str,
        max_report_length: int,
        on_error: ErrorHandlerFn | None = None,
    ):
        """Init method definition."""
        self._model = model
        self._extraction_prompt = extraction_prompt
        self._on_error = on_error or (lambda _e, _s, _d: None)
        self._max_report_length = max_report_length

    async def __call__(self, input_text: str):
        """Call method definition."""
        output = None
        try:
            prompt = self._extraction_prompt.format(**{
                INPUT_TEXT_KEY: input_text,
                MAX_LENGTH_KEY: str(self._max_report_length),
            })
            # NOTE: we intentionally do not pass response_format here.
            #
            # CommunityReportResponse.findings is a list of a nested BaseModel
            # (FindingModel). Pydantic's model_json_schema() - and litellm's
            # type_to_response_format_param(), which builds the wire-level
            # {"type": "json_schema", ...} payload from it - always factors a
            # nested BaseModel out into a top-level "$defs" entry referenced
            # via "$ref"; there is no built-in option to force full inlining.
            #
            # Several OpenAI-"strict"-mode-compatible guided-JSON backends
            # (observed: a Databricks AI Gateway serving an OpenAI OSS model;
            # also known to affect vLLM guided decoding and other
            # outlines/lm-format-enforcer-based servers) only implement the
            # flat subset of JSON Schema and reject any schema containing
            # "$defs"/"$ref" with a 400 (e.g. "Invalid JSON schema -
            # /$defs/FindingModel"), which made every single community report
            # fail on those backends.
            #
            # The prompt already asks for the JSON shape in prose, so instead
            # of relying on server-side structured-output enforcement, we
            # parse and validate the response text ourselves.
            response = await self._model.completion_async(
                messages=prompt,
            )

            output = CommunityReportResponse(**_parse_json_response(response.content))
        except Exception as e:
            logger.exception("error generating community report")
            self._on_error(e, traceback.format_exc(), None)

        text_output = self._get_text_output(output) if output else ""
        return CommunityReportsResult(
            structured_output=output,
            output=text_output,
        )

    def _get_text_output(self, report: CommunityReportResponse) -> str:
        report_sections = "\n\n".join(
            f"## {f.summary}\n\n{f.explanation}" for f in report.findings
        )
        return f"# {report.title}\n\n{report.summary}\n\n{report_sections}"
