# Bug: race condition no cache de arquivos (graphrag 3.1.1)

## Sintoma

`graphrag index` falhava de forma não-determinística durante `extract_graph` ou
`summarize_descriptions`, com erros alternando entre execuções:

```
FileNotFoundError: [Errno 2] No such file or directory: '/workspaces/graphrag/cache/summarize_descriptions/<hash>_v4'
```

```
TypeError: the JSON object must be str, bytes or bytearray, not NoneType
```

Reindexar do zero (apagando `cache/`) "resolvia" temporariamente, mas o erro
voltava em execuções seguintes — sinal de bug de concorrência, não de config
quebrada ou prompt faltando.

## Causa raiz

1. **`concurrent_requests` é um campo global**, não por modelo. O campo real
   está em `GraphRagConfig.concurrent_requests` (raiz do `settings.yaml`), com
   **default = 25**. Colocar `concurrent_requests: 4` dentro de
   `completion_models.local_completion_model` ou `embedding_models.default_embedding_model`
   é **silenciosamente ignorado** — esse campo não existe no schema de
   `ModelConfig`. Confirme com:
   ```bash
   python3 -c "from graphrag.config.models.graph_rag_config import GraphRagConfig; print(GraphRagConfig.model_fields['concurrent_requests'])"
   ```

2. **O cache de arquivos (`FileStorage`) não é atômico.** `set()` abre o
   arquivo direto em modo escrita (`open(path, "w")`), o que trunca o
   conteúdo antes de escrever — sem arquivo temporário + rename.
   Ver `graphrag_storage/file_storage.py`.

3. **`JsonCache.get()` faz check-then-use (TOCTOU):**
   ```python
   async def get(self, key):
       if await self.has(key):  # checa existência
           data = await self._storage.get(key)  # lê depois, em chamada separada
   ```
   `FileStorage.get()` repete o mesmo padrão internamente.

4. Com `concurrent_requests=25`, é comum duas coroutines processarem a
   **mesma chave de cache** ao mesmo tempo — acontece sempre que a mesma
   entidade aparece descrita em vários chunks do corpus (muito comum em
   corpus real). A sequência de eventos observada:
   - Coroutine A abre o arquivo em modo escrita → trunca.
   - Coroutine B lê o arquivo nesse instante → JSON incompleto →
     `JSONDecodeError` → `JsonCache.get` deleta o arquivo.
   - Coroutine C, que já tinha passado o `has()` um instante antes (arquivo
     existia), tenta abrir o arquivo agora deletado → `FileNotFoundError`.
   - Ou: `FileStorage.get()` roda seu próprio `has()` interno e já não
     encontra o arquivo → retorna `None` → `json.loads(None)` no
     `JsonCache.get` → `TypeError: ... NoneType`.

   Esses são exatamente os dois erros observados, então a race está
   confirmada e não é hipotética.

## Correção aplicada

No topo do `settings.yaml` (fora de qualquer bloco), adicionar:

```yaml
concurrent_requests: 3
```

Isso reduz drasticamente (de 25 para 3) a chance de duas coroutines
processarem a mesma chave de cache ao mesmo tempo. `concurrent_requests: 1`
elimina a race por construção (zero concorrência possível) mas é ~3x mais
lento; `3` é o valor validado em produção — reindexação completa (953 chunks,
~9.500 chamadas de `summarize_descriptions`) rodou **sem nenhum erro** do
início ao fim. Se a race reaparecer em outro corpus/execução, caia para `1`
(garantidamente seguro, dado que o cache de arquivos não é atômico) ou
avalie um valor intermediário como `2`.

Como `default_completion_model` (Agnes) já tem `rate_limit: 20/min`, a fase
`extract_graph` já era gargalada pelo rate limit — reduzir a concorrência
não adiciona custo de tempo perceptível ali. O maior impacto é em
`summarize_descriptions`, que roda no modelo local (3090): a essa
concorrência ainda processou ~9.500 itens em poucas horas.

Os campos `concurrent_requests: 4` dentro dos blocos de model foram
removidos por serem inertes (confundem leitura futura do arquivo) — o campo
correto só existe no nível raiz de `GraphRagConfig`.

### Validação (2026-08-08)

- Execução com `concurrent_requests: 1`: chegou a 930/953 em `extract_graph`
  e ~700/12766 em `summarize_descriptions` sem erro, mas ritmo sequencial
  projetava ~13h só para essa fase — interrompida por ser lenta demais, não
  por falha.
- Execução com `concurrent_requests: 3` (índice do zero, cache limpo):
  completou `extract_graph` (953/953), `summarize_descriptions` (9488/9488),
  `create_communities`, `create_community_reports` (todos os níveis 0-4) e
  `generate_text_embeddings` — **zero erros** do início (00:58) ao fim
  (`Pipeline complete`). Resultado: 4.165 entidades, 4.967 relações, 635
  comunidades/relatórios, embeddings com dim=1024 nas 3 tabelas do LanceDB
  (`entity_description`, `text_unit_text`, `community_full_content`).

## Bug latente adicional encontrado (nomes de arquivo)

`settings.yaml` referenciava:
```yaml
community_reports:
  graph_prompt: "prompts/community_report_graph.txt"
  text_prompt: "prompts/community_report_text.txt"
```

mas esses arquivos nunca tinham sido criados com esse nome (só existia
`prompts/community_reports.txt`, com o prompt errado — só a versão "graph").
Isso ainda não tinha disparado porque o pipeline sempre falhava antes, na
fase `extract_graph`. Corrigido gerando os dois arquivos corretos a partir de:
- `graphrag.prompts.index.community_report.COMMUNITY_REPORT_PROMPT` → `community_report_graph.txt`
- `graphrag.prompts.index.community_report_text_units.COMMUNITY_REPORT_TEXT_PROMPT` → `community_report_text.txt`

## Se o bug voltar a aparecer

- Confirme que `concurrent_requests: 1` está no nível raiz do `settings.yaml`
  (não dentro de `completion_models:` ou `embedding_models:`).
- Se quiser mais paralelismo, `concurrent_requests: 2` ou `3` reduz a chance
  da colisão mas não a elimina — só `1` é garantidamente seguro dado que o
  cache de arquivos não é atômico.
- Alternativa (sem cache, sem reuso entre execuções): `cache: { type: noop }`
  no lugar de `type: json` — evita I/O de arquivo por completo, mas qualquer
  interrupção obriga reprocessar tudo do zero.
