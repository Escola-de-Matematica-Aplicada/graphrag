# PR candidate: normalize list-style `content` from reasoning models (`litellm`, upstream BerriAI/litellm)

Conteúdo em inglês de propósito — é material pronto para virar a descrição
de um PR no repositório upstream (`github.com/BerriAI/litellm`). O diff
real (gerado a partir de um `litellm==1.92.0` pristino vs. o mesmo pacote
depois de rodar `../scripts/apply-litellm-reasoning-patch.py`) está em
`patches/01-litellm-types-utils.py.diff`. Contexto completo e o bug
original (outro modelo, mesma causa raiz): `BUGFIX-LITELLM-REASONING.md`.

---

## Summary

`litellm.types.utils.Message.__init__` assumes `content` is always
`Optional[str]`. Some providers/models return it as a list of typed blocks
instead — this crashes with a `pydantic_core.ValidationError` on every
single call, not a soft degradation.

## Repro

```python
import asyncio, litellm


async def main():
    resp = await litellm.acompletion(
        model="openai/<a reasoning model behind an OpenAI-compatible gateway>",
        api_base="...",
        api_key="...",
        messages=[{"role": "user", "content": "hello"}],
    )


asyncio.run(main())
```

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Message
content
  Input should be a valid string [type=string_type, input_value=[{'type': 'reasoning', ...}], input_type=list]
```

Confirmed on two independent reasoning models behind two different
OpenAI-compatible gateways (both routed through litellm's generic `openai`
provider, so this is not provider-specific code in litellm — it's a gap in
response normalization):

- `qwen35-122b-a10b` via a Databricks AI Gateway endpoint (original report,
  see `BUGFIX-LITELLM-REASONING.md`)
- `gpt-oss-120b` via the same kind of gateway (this PR's reproduction)

Both return the harmony-style shape:
```json
[{"type": "reasoning", "summary": [{"type": "summary_text", "text": "..."}]},
 {"type": "text", "text": "the actual answer"}]
```

## Fix

In `Message.__init__`, before building `init_values`: if `content` is a
list, extract and join the `"text"`-typed blocks into a single string.
Only fall back to the `"reasoning"` block's text if there is no `"text"`
block at all (some truncated/`finish_reason="length"` responses only ever
produce reasoning, never reach a final answer — in that case a caller
still gets *something* instead of a hard crash, and can inspect
`finish_reason` to detect the truncation).

```python
if isinstance(content, list):
    text_parts: List[str] = []
    reasoning_parts: List[str] = []
    for item in content:
        if isinstance(item, dict):
            if item.get("type") == "text" and "text" in item:
                text_parts.append(item["text"])
            elif item.get("type") == "reasoning" and "summary" in item:
                for s in item.get("summary", []):
                    if isinstance(s, dict) and "text" in s:
                        reasoning_parts.append(s["text"])
        elif isinstance(item, str):
            text_parts.append(item)
    content = (
        "\n".join(text_parts) if text_parts else ("\n".join(reasoning_parts) or None)
    )
```

Full diff: `patches/01-litellm-types-utils.py.diff`.

## Why prefer the `"text"` block over concatenating everything

An earlier version of this fix (documented internally, see
`BUGFIX-LITELLM-REASONING.md`) always concatenated reasoning text + final
text. That is *lossy in a different way* for any downstream consumer that
parses the response with a positional/delimiter-based format (e.g.
structured-extraction prompts that ask for a specific tuple syntax): the
reasoning prose gets glued directly in front of the first token of the real
answer with no delimiter, corrupting whatever the first parsed unit is.
Preferring the final `"text"` block avoids that class of bug entirely and
still degrades gracefully (falls back to reasoning text) for the case the
original fix targeted — a response that never produced a text block at
all.

## How to apply

```bash
python3 apply-litellm-reasoning-patch.py   # ../scripts/apply-litellm-reasoning-patch.py — idempotent
```

or apply `patches/01-litellm-types-utils.py.diff` directly with `patch -p1`
/ `git apply` from a `litellm` checkout.

## Test evidence

Before patch: 100% failure rate (crash) on every call to `gpt-oss-120b`
through the affected gateway, trivial prompt or real-world prompt alike.

After patch: 0 crashes across ~6 calls (trivial prompt, and a real
~1,400-word biography-extraction prompt at `max_tokens=8192`, at three
different `reasoning_effort` levels). `finish_reason: "stop"` in all cases,
correct final-answer text recovered from the `"text"` block, reasoning
block correctly discarded. See
`../../graphrag-operations/references/edge-label-patch.md` (section
"gpt-oss-120b via gateway Databricks") for the full comparison table
against a non-reasoning baseline model.
