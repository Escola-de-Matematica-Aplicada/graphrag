# Bug: litellm falha ao parser respostas com reasoning content

## Sintoma

`graphrag prompt-tune` falha em `generate_entity_types` com:

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Message
content
  Input should be a valid string [type=string_type, input_value=[{'type': 'reasoning', ...}], input_type=list]
```

ou:

```
JSONSchemaValidationError: litellm.JSONSchemaValidationError: model=, returned an invalid response
```

O modelo qwen35-122b-a10b (Databricks) retorna `content` como lista de blocos de reasoning em vez de string, e a resposta inclui texto de raciocínio antes do JSON — quebrando o parser do litellm.

## Causa raiz

1. **`litellm/types/utils.py` — `Message.__init__`**: o campo `content` é tipado como `Optional[str]`, mas o Databricks/qwen35 retorna `[{'type': 'reasoning', 'summary': [...]}, {'type': 'text', 'text': '...'}]`. O pydantic rejeita list onde espera str.

2. **`litellm/litellm_core_utils/json_validation_rule.py` — `validate_schema`**: `json.loads(response)` falha quando a response string começa com "Thinking Process:" em vez de `{`.

## Versão atualizada do Patch 1 (2026-09-22)

`../scripts/apply-litellm-reasoning-patch.py` aplica uma versão melhorada
do Patch 1 abaixo: em vez de sempre concatenar reasoning+texto (o que gruda
o parágrafo de raciocínio direto na frente do primeiro token da resposta
real, sem o `record_delimiter` do graphrag entre eles — corrompe o primeiro
registro extraído em consumidores como `graph_extractor.py`), prefere só o
bloco `"text"` final e cai para o texto de `"reasoning"` apenas se não
houver bloco de texto nenhum. Confirmado funcionando com `gpt-oss-120b`
(ver `graphrag-operations/references/edge-label-patch.md`, seção
"gpt-oss-120b"). Prefira rodar o script a aplicar o patch manual abaixo.

## Fix aplicado (2026-09-18)

### Patch 1: `litellm/types/utils.py` — Message.__init__

Adicionar após a atribuição de `reasoning_content` e antes do `super().__init__`:

```python
# Handle list content (e.g., Databricks/qwen35 reasoning format)
if isinstance(init_values.get("content"), list):
    parts = []
    for item in init_values["content"]:
        if isinstance(item, dict):
            if item.get("type") == "text" and "text" in item:
                parts.append(item["text"])
            elif item.get("type") == "reasoning" and "summary" in item:
                for s in item.get("summary", []):
                    if isinstance(s, dict) and "text" in s:
                        parts.append(s["text"])
        elif isinstance(item, str):
            parts.append(item)
    init_values["content"] = "\n".join(parts) if parts else None
```

### Patch 2: `litellm/litellm_core_utils/json_validation_rule.py` — validate_schema

Adicionar antes do `json.loads`:

```python
import re as _re

# Extract JSON object from response if it contains reasoning/text before the JSON
_json_str = response.strip()
if not _json_str.startswith("{"):
    # Try to find {"entity_types": ... } pattern first, then fall back to first {...}
    _m = _re.search(r"(\{\"entity_types\".*?\})", _json_str, _re.DOTALL)
    if not _m:
        _m = _re.search(r"(\{.*\})", _json_str, _re.DOTALL)
    if _m:
        _json_str = _m.group(1)
```

**Importante**: usar `_re.DOTALL` (não `re.DOTALL`) porque o import é `import re as _re`.

## Workaround alternativo

Rodar `prompt-tune` com `--no-discover-entity-types` para bypassar o passo `generate_entity_types` que falha. Os entity types devem ser especificados manualmente no `settings.yaml` sob `extract_graph.entity_types`.

## Validação

Após aplicar os patches, limpar cache Python:

```bash
find /usr/local/lib/python3.12/site-packages/litellm -name "*.pyc" -delete
find /usr/local/lib/python3.12/site-packages/litellm -name "__pycache__" -type d -exec rm -rf {} +
```

Rodar prompt-tune e verificar se os prompts são gerados em `prompts/`:

```bash
ls -la prompts/
# extract_graph.txt  summarize_descriptions.txt  community_report_graph.txt  community_report_text.txt
```