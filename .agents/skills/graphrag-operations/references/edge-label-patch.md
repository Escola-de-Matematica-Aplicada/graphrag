# Patch: label curta (3 palavras) nas arestas + causa raiz do grafo sem descrição

> **Nota (fork Escola-de-Matematica-Aplicada/graphrag):** este patch já está
> mergeado diretamente no código-fonte deste fork (`packages/graphrag/graphrag/
> data_model/schemas.py`, `.../index/operations/extract_graph/graph_extractor.py`,
> `.../extract_graph.py`, `.../index/operations/snapshot_graphml.py`). O script
> `../scripts/apply-edge-label-patch.py` e os diffs em `patches/` continuam
> úteis apenas para reaplicar o mesmo patch num pacote `graphrag` instalado via
> pip/site-packages (outra máquina, container efêmero, ou o `microsoft/graphrag`
> upstream ainda não patchado) — não são necessários neste checkout.

Sessão real (2026-09-18, graphrag 3.1.2 instalado globalmente sem venv,
corpus DHBB/CPDOC — biografias políticas em português, entity_types
`pessoa,partido,ideologia`). Objetivo: o `graph.graphml` saía sem nenhuma
informação semântica nas arestas (só `weight`), e o pedido era acrescentar
uma label curta (~3 palavras) por relação. Documentando aqui porque a causa
raiz e o patch envolvem 4 arquivos do pacote instalado, e dois dos bugs só
aparecem em corpus real (não aparecem em `--method fast`).

## Causa raiz nº 1 — `graph.graphml` sempre sai sem `description`

`graphrag/index/operations/snapshot_graphml.py` (linha ~17):

```python
graph = nx.from_pandas_edgelist(edges, edge_attr=["weight"])
```

`relationships.parquet` **já tem** uma coluna `description` (gerada na
extração, resumida em `summarize_descriptions`), mas o snapshot para
`.graphml` só copia `weight`. A descrição sempre existiu nos dados — só é
descartada nesse passo específico. Fix mínimo (inclui qualquer atributo
textual que exista, sem quebrar quando não existe):

```python
edge_attrs = [
    attr for attr in ("weight", "label", "description") if attr in edges.columns
]
graph = nx.from_pandas_edgelist(edges, edge_attr=edge_attrs)
```

## Armadilha nova — GraphML não aceita `None`/NaN

Com o fix acima, o pipeline quebra em `finalize_graph` (só aparece com corpus
real, não em `--method fast` fora de casos com dados esparsos):

```
networkx.exception.NetworkXError: GraphML writer does not support <class 'NoneType'> as data values.
```

`description`/`label` vêm `None`/NaN em várias linhas (nem toda extração usa
LLM — `extract_graph_nlp` nunca popula nenhum dos dois; mesmo com LLM,
`label` fica `None` sempre que o texto não tem relações). Fix: `fillna("")`
nas colunas de texto (nunca em `weight`, que é numérico) antes de montar o
grafo:

```python
text_attrs = [attr for attr in edge_attrs if attr != "weight"]
if text_attrs:
    edges = edges.copy()
    edges[text_attrs] = edges[text_attrs].fillna("")
graph = nx.from_pandas_edgelist(edges, edge_attr=edge_attrs)
```

## Acrescentando um campo customizado (`label`) ao formato de relação

O parser da extração (`graph_extractor.py`) espera exatamente
`("relationship"<|>source<|>target<|>description<|>strength)` — 5 campos,
`weight = float(record_attributes[-1])` (sempre o último), `description =
record_attributes[3]` (índice fixo). Para acrescentar um `label` sem quebrar
compatibilidade, ele entra **entre** `description` e `strength` (6 campos),
porque `weight` já é lido por `[-1]` (robusto a campos extras) mas
`description` é lido por índice fixo `[3]` (não pode mudar de posição).

Precisa editar **4 arquivos** — esquecer um quebra silenciosamente (o campo
some sem erro, ou o pipeline crasha em outro workflow):

1. **`data_model/schemas.py`** — nova constante e inclusão no schema final:
   ```python
   EDGE_LABEL = "label"  # junto de EDGE_WEIGHT, EDGE_DETAILS etc.

   RELATIONSHIPS_FINAL_COLUMNS = [
       ID,
       SHORT_ID,
       EDGE_SOURCE,
       EDGE_TARGET,
       DESCRIPTION,
       EDGE_LABEL,  # <- nova
       EDGE_WEIGHT,
       EDGE_DEGREE,
       TEXT_UNIT_IDS,
   ]
   ```

2. **`index/operations/extract_graph/graph_extractor.py`** — parser aceita o
   6º campo opcional (fallback para tuplas antigas de 5 campos):
   ```python
   if record_type == '"relationship"' and len(record_attributes) >= 5:
       ...
       edge_label = clean_str(record_attributes[4]) if len(record_attributes) >= 6 else ""
       ...
       relationships.append({..., "label": edge_label, ...})
   ```
   Também atualizar `_empty_relationships_df()` para incluir `"label"` nas
   colunas — senão um chunk sem relações produz um DataFrame sem essa coluna
   e o `pd.concat` mais adiante gera `NaN`/schema inconsistente.

3. **`index/operations/extract_graph/extract_graph.py::_merge_relationships`
   — A ARMADILHA MAIS TRAIÇOEIRA.** Esta função faz
   `.groupby(["source","target"]).agg(description=..., text_unit_ids=...,
   weight=...)` com uma **lista de colunas fixa no código-fonte**. Qualquer
   coluna nova (`label`) desaparece silenciosamente aqui — sem erro, sem
   warning, o campo simplesmente não existe mais no DataFrame de saída. Esse
   é o passo que mescla os resultados de todos os chunks/text_units em um
   único grafo; é fácil confirmar o patch dos outros 3 arquivos e mesmo assim
   o `label` sair `None` no `relationships.parquet` final — a causa está
   aqui, não no parser. Fix:
   ```python
   def _merge_relationships(relationship_dfs) -> pd.DataFrame:
       all_relationships = pd.concat(relationship_dfs, ignore_index=False)
       agg = {
           "description": ("description", list),
           "text_unit_ids": ("source_id", list),
           "weight": ("weight", "sum"),
       }
       if "label" in all_relationships.columns:
           agg["label"] = ("label", "first")
       return (
           all_relationships
           .groupby(["source", "target"], sort=False)
           .agg(**agg)
           .reset_index()
       )
   ```
   Diagnóstico que confirma a causa: rode o pipeline só até `extract_graph`
   (remova `prune_graph`/`finalize_graph` da lista `workflows:`) e leia
   `output/relationships.parquet` direto — se a coluna `label` nem existir
   (`KeyError` ao acessar), é este `.agg()`; se existir mas vier `None`, é
   outro passo (dedup em `finalize_relationships`, merge de summarize, etc.).

4. **`index/operations/snapshot_graphml.py`** — ver causa raiz nº 1 acima
   (incluir `label` no `edge_attr`).

Depois desse patch, o prompt de extração (`prompts/extract_graph.txt`) tem
que pedir o 6º campo explicitamente — ver seção de prompt abaixo.

## Redigindo a instrução do label no prompt — evite "EXACTLY N words"

Formato pedido ao LLM (Steps, dentro do prompt de `extract_graph`):
```
- relationship_label: a very short label for this relationship, in Portuguese,
  ideally three words (never more than four), as a verb phrase summarizing
  the relationship — do not overthink the exact word count, just keep it brief
  (e.g., "filiou-se ao partido", "apoiou reforma eleitoral")
Format each relationship as ("relationship"<|><source_entity><|><target_entity><|><relationship_description><|><relationship_label><|><relationship_strength>)
```

**Armadilha real**: a primeira versão pedia `EXACTLY three words`. Com um
modelo de raciocínio (`qwen35-122b-a10b`/Agnes), isso disparou um loop
obsessivo de recontagem — o `content` da resposta ficava cheio de
`"filiou-se" (1), "ao" (2), "partido" (3). Total 3 words. Let's look at
Example 1..."` repetidas vezes, sem nunca emitir a tupla formatada
(`finish_reason: "length"` em ~50% das chamadas mesmo com `max_tokens:
16000`). Trocar para "ideally... never more than... do not overthink"
resolveu — ver seção de modelo abaixo para o resto do diagnóstico (o
problema real era o modelo, não só a frase).

**Nota de cross-reference**: existe um bug *diferente* do mesmo modelo
(`qwen35-122b-a10b`) documentado em
`.agents/skills/graphrag-strategy/references/BUGFIX-LITELLM-REASONING.md`
— lá o `content` vem como **lista** de blocos (`[{"type":"reasoning",...},
{"type":"text",...}]`) e quebra o parser do litellm com
`pydantic_core.ValidationError`; o patch deles extrai só o bloco `"text"`.
Isso resolve quando o modelo *termina* a geração com um bloco de texto
separado. O caso descrito aqui é mais grave: o `content` já vem como
**string simples**, mas a geração é cortada por `max_tokens` **antes** de
qualquer bloco de texto existir — só "Thinking Process..." do início ao
fim. Não tem o que extrair; a única correção é trocar de modelo (ver
tabela abaixo) ou aumentar `max_tokens` a ponto do modelo nunca estourar
(caro e não determinístico — não recomendado).

## Escolha de modelo para extração estruturada — evite variantes "thinking"

Sintoma: `finish_reason: "length"` no `content` cacheado
(`cache/extract_graph/*.json` →
`result.response.choices[0].finish_reason`),Resposta cheia de
`"Thinking Process: 1. Analyze the Request..."` e nenhuma tupla
`("entity"<|>...)`/`("relationship"<|>...)` chegando a ser emitida →
`Pipeline error: Graph Extraction failed. No entities detected during
extraction.` mesmo com `max_gleanings: 0` e um prompt correto.

Causa: modelo de raciocínio (`qwen35-122b-a10b`, chamado internamente de
"Agnes" neste ambiente) gasta o orçamento inteiro de `max_tokens`
"pensando" em português sobre a tarefa antes de chegar à resposta formatada
— em textos biográficos densos (múltiplas entidades, muitas relações),
nunca sobra espaço. Subir `max_tokens` (testado até 16000) não resolveu:
o modelo simplesmente pensa mais, proporcionalmente.

**O mesmo bug corrompeu um `prompt-tune` anterior** (não relacionado à
extração): o "Thinking Process" inteiro do modelo foi capturado como parte
do *exemplo* few-shot gerado em `prompts/extract_graph.txt`/
`community_report_graph.txt`/`summarize_descriptions.txt` (arquivos de
1000+ linhas, quase tudo monólogo confuso do tipo "Wait, I need to check...
Actually, looking closer..."). Sintoma de detecção rápida:
```bash
grep -c "Thinking Process\|Wait, I need to check" prompts/*.txt
```
Qualquer contagem > 0 significa que o prompt está corrompido e não deve ser
usado como base — reescrever do zero (ou copiar o template default do
pacote: `graphrag.prompts.index.<nome>.<CONST>_PROMPT`) é mais rápido que
tentar remendar.

**Fix**: trocar o modelo de completion por uma variante **instruct** (sem
chain-of-thought). Modelos observados neste ambiente
(`system.ai.*` via gateway Databricks/litellm):

| Modelo | Tipo | Uso recomendado |
|---|---|---|
| `qwen35-122b-a10b` | *thinking*, MoE 122B/A10B | Evitar para extração estruturada — ver acima |
| `qwen3-next-80b-a3b-instruct` | instruct (sem thinking), MoE 80B/A3B | Bom para tarefas leves (`summarize_descriptions`) |
| `llama-4-maverick` | instruct, MoE grande (Meta) | **Melhor escolha validada para `extract_graph`/`community_reports`** — maior capacidade da lista, sem vazamento de raciocínio. **Teto de `max_tokens: 8192`** (erro `litellm.BadRequestError: max_tokens (16000) cannot exceed 8192` se passar disso) |
| `meta-llama-3-3-70b-instruct` | instruct, denso 70B | Alternativa solida, menor capacidade que o Maverick |
| `gemma-3-12b` | instruct, denso 12B | Pequeno demais para extração multi-entidade em texto longo |
| `qwen3-embedding-0-6b` | embedding | Não é modelo de completion — usar só em `embedding_models` |
| `gpt-oss-120b` | *thinking* (harmony), MoE | Ver seção "gpt-oss-120b" abaixo — quebra sem patch adicional, e não é mais rápido que o Maverick na prática para extração |

### `gpt-oss-120b` via gateway Databricks — crasha sem patch, e não é mais rápido na prática

Testado em 2026-09-22 depois que o Databricks anunciou esse modelo como
"mais rápido que o Llama 4" — a expectativa era trocar o
`default_completion_model` de `llama-4-maverick` para ele. Dois problemas
reais encontrados **antes** de comprometer qualquer rodada de produção:

1. **Crasha com `pydantic_core.ValidationError` em toda chamada via
   `litellm.acompletion`** (o caminho real que o `graphrag` usa, não um
   `curl` cru) — mesma classe de bug do
   `BUGFIX-LITELLM-REASONING.md` (content vem como lista
   `[{"type":"reasoning",...}, {"type":"text",...}]`, pydantic espera
   string). Fix: `references/../graphrag-strategy/scripts/apply-litellm-reasoning-patch.py`
   aplica uma versão melhorada do patch (prefere o bloco `"text"` final e só
   cai para o texto de `"reasoning"` se não houver bloco de texto nenhum —
   evita colar o parágrafo de raciocínio direto na frente do primeiro
   registro extraído, o que corrompe a primeira tupla).
2. **Não é mais rápido para extração estruturada, mesmo depois do patch** —
   comparação real, mesmo chunk de ~1.400 palavras, mesmo prompt de produção
   (`max_tokens: 8192`):
   | Config | Tempo | `completion_tokens` | Entidades/relações extraídas |
   |---|---|---|---|
   | `llama-4-maverick` (baseline produção) | 12,3s | 998 | 15 / 7 |
   | `gpt-oss-120b`, `reasoning_effort` default (medium) | 24,0s | 4.765 | 22 / 16 |
   | `gpt-oss-120b`, `reasoning_effort: low` | 12,8s | 1.907 | 13 / 12 |
   | `gpt-oss-120b`, `reasoning_effort: minimal` | 18,5s | 3.618 | 23 / 30 |

   Sem configurar `reasoning_effort` explicitamente, é **~2x mais lento e
   gasta ~4,8x mais tokens de saída** que o Maverick para a mesma tarefa —
   o "mais rápido" do Databricks provavelmente se refere a throughput bruto
   de tokens/s do serving, não ao tempo total de uma tarefa que precisa de
   muito mais tokens de saída (raciocínio) para o mesmo resultado.
   `reasoning_effort: low` empata em tempo com o Maverick, mas exige
   `allowed_openai_params: ["reasoning_effort"]` no `call_args` (senão
   `litellm.UnsupportedParamsError`, o provider `openai` não permite esse
   param por padrão) — e **a contagem de entidades/relações varia de forma
   não-monotônica entre os níveis de esforço** (13→22→23 entidades,
   low→medium→minimal, nem sempre "menos esforço = extração mais enxuta"),
   um sinal de inconsistência que o Maverick não mostrou em produção
   (1.580/1.580 chamadas sem falha, ver seção de rate limit acima).

   **Conclusão**: não trocar o modelo de produção para `gpt-oss-120b` sem
   testar num lote maior (dezenas de chunks reais) e sem decidir
   explicitamente um `reasoning_effort` fixo — o ganho de velocidade
   alegado não se confirmou no caso de uso de extração estruturada.

## Cache não invalida ao trocar de modelo

`cache/extract_graph/*.json` é indexado por hash de `(prompt, texto de
entrada)` — **não** inclui o `model` configurado. Trocar
`default_completion_model` no `settings.yaml` e rodar de novo **reaproveita
respostas antigas do modelo anterior silenciosamente** (o `graphrag index`
termina "rápido" e sem erro, mas na verdade não chamou o novo modelo
nenhuma vez). Sinal de alerta: rerun após troca de modelo completa em
segundos quando devia levar minutos. Sempre `rm -rf cache/extract_graph
cache/summarize_descriptions` (ou `cache/` inteiro) depois de trocar
`completion_models` num root que já tem cache de uma extração anterior.

## Validação recomendada (root isolado, mini-corpus de ~10 arquivos)

1. `workflows:` restrito a `load_input_documents, create_base_text_units,
   create_final_documents, extract_graph, finalize_graph` (sem
   `prune_graph` — ver próxima seção; sem `community_reports`/embeddings).
2. Prompt de extração já com o campo de label (ver seção acima).
3. Rodar, depois:
   ```python
   import pandas as pd

   rels = pd.read_parquet("output/relationships.parquet")
   print(rels["label"].notna().sum(), "/", len(rels))
   ```
4. Conferir o `.graphml`: `grep -m3 "<edge" output/graph.graphml` deve
   mostrar 3 `<data>` (weight/label/description), não só 1.

## Validação completa: Fase 3 (pipeline padrão) + `drift_search` no mesmo mini-corpus

Depois dos 4 patches + troca de modelo, rodei o pipeline `--method standard`
inteiro (sem restringir `workflows:`) no mesmo mini-corpus de 10 arquivos —
inclui `create_communities`, `create_community_reports` e
`generate_text_embeddings`, não só `extract_graph`/`finalize_graph`. Resultado:
102/102 relações finais com `label` preenchido, 13 comunidades,
`community_reports.parquet` e `lancedb/` gerados sem nenhum erro na rodada
(erros de tentativas anteriores continuam acumulados no mesmo
`logs/indexing-engine.log` — sempre conferir o *timestamp* do erro contra o
timestamp da rodada atual antes de assumir que ela falhou).

Tempos (`output/stats.json`, campo `workflows.<nome>.overall`, 10 documentos):
`extract_graph` 210,8s (64% do total) e `create_community_reports` 98,0s (30%)
dominam — as duas únicas etapas com chamada de LLM real. Todo o resto
(grafo, comunidades, embeddings) soma <6% do tempo total (330,7s). Útil para
estimar custo antes de indexar o corpus de produção: a extrapolação linear
pelo nº de documentos é só um piso, porque `create_community_reports` escala
com nº de *comunidades*, não de documentos.

**`drift_search` não estava configurado** neste `settings.yaml` de teste —
seção ausente por padrão, precisa ser adicionada manualmente (copiar o bloco
do `settings.yaml` de produção, com `completion_model_id` apontando para um
modelo **instruct**, não thinking). Com `llama-4-maverick`, uma sequência de
3 iterações manuais de `drift` (tema ideologia/regime militar, ver
`graphrag-strategy` skill para o padrão de iteração) rodou sem nenhum
`JSONDecodeError` e sem repetir a mesma resposta — cada iteração trouxe fatos
novos coerentes com o grafo. Confirma que a troca de modelo (armadilha 10)
resolve o problema de raciocínio/JSON também em `drift_search`, não só em
`extract_graph`.

## Rate limit de um modelo secundário derruba todo o `extract_graph` — mesmo com o principal 100% ok

Escala real: corpus de 1.109 documentos (amostra DHBB), `extract_graph` via
`llama-4-maverick` rodou **3h23min, 1580/1580 chamadas com sucesso** (337
retries, 0 falhas — modelo robusto até em alto volume). Mas o workflow
`extract_graph` inclui internamente o passo `summarize_descriptions`
(mescla descrições de entidades/relações repetidas entre chunks), configurado
para um modelo **diferente** (`local_completion_model`, na época
`qwen3-next-80b-a3b-instruct`, sem bloco `rate_limit:` no `settings.yaml`).
Esse modelo bateu numa cota do workspace Databricks:
```
litellm.RateLimitError: OpenAIException - REQUEST_LIMIT_EXCEEDED: Exceeded
workspace output tokens per minute rate limit for
databricks-qwen3-next-80b-a3b-instruct.
```
Isso propagou como exceção fatal do workflow `extract_graph` inteiro —
**as 3h23min de extração bem-sucedida do Maverick foram descartadas**
(nenhum `entities.parquet`/`relationships.parquet` chegou a ser escrito;
só `documents.parquet`/`text_units.parquet`, das etapas anteriores).

Duas lições:

1. **Toda chamada de LLM dentro de `extract_graph` (extração E
   `summarize_descriptions`) precisa de `rate_limit:` configurado** — não só
   o `completion_model_id` principal. Um modelo secundário sem `rate_limit:`
   pode saturar sua própria cota (client-side sem throttle → servidor
   rejeita) e derrubar o workflow inteiro, mesmo que o modelo principal
   esteja indo bem.
2. **O cache salva a sessão**: como o cache de `extract_graph` é
   por-chamada (não por-workflow), religar `graphrag index` no mesmo root
   reaproveita as 1580 chamadas já feitas ao Maverick (cache hit,
   segundos) e só precisa refazer o `summarize_descriptions` que faltou —
   mas antes de religar, **limpar só `cache/summarize_descriptions/`** (não
   `cache/extract_graph/`) se for trocar o modelo desse passo — lembrar da
   armadilha do cache não incluir o modelo na chave (ver acima).

Fix aplicado: trocado `local_completion_model` de `qwen3-next-80b-a3b-instruct`
para `meta-llama-3-3-70b-instruct` (cota separada da do Qwen/Maverick) +
adicionado `rate_limit: {{type: sliding_window, period_in_seconds: 60,
requests_per_period: 20}}` (mesmo perfil do `default_completion_model`).

## Cota DIÁRIA (não por minuto) — específica do workspace Databricks (tier gratuito) — derruba um workflow de horas

**Importante para quem for escolher provedor**: essa cota diária é uma
característica do **workspace Databricks (AI Gateway, tier gratuito)**
especificamente — não é uma limitação do GraphRAG, do litellm, nem
inerente a LLMs em geral. Outros provedores (Agnes AI, e presumivelmente
qualquer endpoint com throughput provisionado/pago) não exibiram esse
comportamento nesta sessão. Ao decidir onde rodar lotes grandes e longos,
tratar "Databricks free tier" como tendo esse teto diário conhecido, não
só o rate limit por minuto já documentado na armadilha anterior.

Escala real onde isso apareceu: corpus **completo** DHBB (7.863 documentos,
10.865 chunks), `extract_graph` via `llama-4-maverick` **no workspace
Databricks**. Depois de
~16h de execução (chegou a 8.296/10.865, ~76%), a resposta mudou de
`RateLimitError` (a mesma armadilha #11 acima, recuperável com retry) para:

```
litellm.BadRequestError: OpenAIException - BAD_REQUEST: Sorry, cannot run
or query foundation model endpoints because you have hit your free daily
limit. Please come back again tomorrow.
```

Diferença crucial: isso é um **`BadRequestError`, não `RateLimitError`** —
não é uma janela deslizante que libera em segundos/minutos, é uma cota
diária do workspace. Retry com backoff exponencial (8 tentativas
configuradas) não ajuda; toda tentativa subsequente falha igual até o
próximo dia.

**Duas camadas de dano diferentes no mesmo evento:**

1. Dentro da extração propriamente dita (`graph_extractor.py`, tem
   try/except por chunk — ver código-fonte citado acima): cada chunk que
   falhar só perde as próprias entidades/relações (~1.868 de 10.865 chunks
   ficaram vazios, ~17%) — **degradação parcial, não fatal**.
2. Mas o workflow `extract_graph` continuou até processar os 10.865/10.865
   chunks (a extração em si terminou!) e emendou direto em
   `summarize_descriptions` — que **não tem proteção por-item**. A cota
   ainda zerada, a *primeira* chamada de summarize (`1/90.809`) falhou e
   isso propagou como excecão fatal do workflow inteiro. Resultado: **as
   ~8.999 chamadas de extração bem-sucedidas (mais 1.580 do cache de uma
   rodada anterior) foram descartadas** — nenhum `entities.parquet`/
   `relationships.parquet` foi escrito. ~39h de tempo de computação
   acumulado do LLM (`compute_duration_seconds` nas métricas) perdidas.
   Mesmo bug estrutural da armadilha "rate limit de um modelo secundário"
   acima, mas agora manifestado dentro do *mesmo* modelo, numa escala
   ~10x mais cara.

**Recuperação confirmada**: chamadas que falham **não são cacheadas** — só
`successful_response_count` gera entrada em `cache/extract_graph/`
(confirmado comparando a contagem de arquivos de cache com as métricas de
`attempted_request_count`/`failed_response_count` do log). Isso significa
que basta trocar de modelo/provedor no `settings.yaml` e rodar
`graphrag index` de novo: os chunks já bem-sucedidos são reaproveitados via
cache-hit (a chave não inclui o modelo — ver armadilha do cache acima, aqui
isso ajuda em vez de prejudicar), e só os que falharam (mais o
`summarize_descriptions`, que nunca chegou a escrever nada) são refeitos
com o modelo novo.

**Fix aplicado**: trocado `default_completion_model` de
`system.ai.llama-4-maverick` (Databricks, tier gratuito, cota diária) para
`agnes-3.0-flash` via **provedor completamente diferente**
(`https://apihub.agnes-ai.com/v1`, chave própria `AGNES_API_KEY`) — cota
independente da do Databricks. Testado com `curl` direto (request minúsculo,
depois um com `max_tokens` igual ao configurado) antes de religar o
`graphrag index`, para não descobrir um erro de endpoint/modelo só depois de
comprometer outra rodada de horas.

**Lição geral**: para jobs em lote de muitas horas usando cota gratuita de
qualquer provedor, ter um modelo de *fallback* em outro provedor pronto no
`settings.yaml` (mesmo que não seja usado por padrão) economiza o tempo de
diagnóstico quando (não "se") a cota travar no meio de um corpus grande.

### Segundo fallback registrado (NVIDIA NIM) — ainda não funcional

Acrescentado `fallback_completion_model` nos `settings.yaml` (teste e
produção), apontando para `nvidia/llama-3.1-nemotron-70b-instruct` via
`https://integrate.api.nvidia.com/v1` (`NVIDIA_API_KEY`) — outro provedor
gratuito, modelo próprio da NVIDIA, instruct (sem chain-of-thought), como
segunda opção de reserva caso o Agnes também esgote cota.

**Não testar às pressas**: `GET /v1/models` com essa chave funciona (lista
82 modelos), mas `POST /v1/chat/completions` retornou
`403 Forbidden {"detail":"Authorization failed"}` em **todos os ~9 modelos
testados** (vários donos: NVIDIA, IBM, Google, Meta) — não é problema de
escolher o modelo certo, é a chave sem entitlement de inferência ativado
(modelos genuinamente descontinuados no catálogo retornam 410/404, erro
diferente, confirmando que o 403 é específico de autorização). Provável
necessidade de ativar a chave pelo portal `build.nvidia.com` antes de
funcionar. **Sempre validar com `curl` antes de colocar em produção** — é
exatamente o que evitou perder outra rodada de horas aqui.

### Atualização — chave nova, e o teste trivial "diga ok" mascara o bug real

Chave da NVIDIA renovada (a anterior tinha vencido) resolveu o 403 em
alguns modelos, mas trouxe outro erro: `404 Function ... Not found for
account` — a conta tem acesso a só um subconjunto dos modelos listados
(entitlement por modelo, não global). Dos ~15 testados dessa vez, só dois
autorizaram: `nvidia/nemotron-3-super-120b-a12b` e
`nvidia/nemotron-3-ultra-550b-a55b` (`openai/gpt-oss-20b`, de outro dono,
também autorizou). `moonshotai/kimi-k3` e `z-ai/glm-5.3-flash` conectam mas
nunca respondem (timeout de 60s, 0 bytes) — parecem não estar "aquecidos"
nessa conta/tier; não valem a pena para uso em produção sem confirmar
disponibilidade antes.

**Armadilha nova, importante**: testar um provedor/modelo novo só com um
prompt trivial (`"responda apenas: ok"`) **não detecta o bug de
raciocínio** — tanto `nemotron-3-super-120b-a12b` quanto
`nemotron-3-ultra-550b-a55b` responderam "ok" corretamente nesse teste
trivial, mas com o prompt de extração real (texto + formato de tupla
pedido), o `nemotron-3-super` reproduziu o bug clássico (`content` idêntico
ao `reasoning_content`, cheio de "Okay, let me check the instructions
again...", `finish_reason: length`, zero tuplas). **Sempre validar contra
o prompt real de produção, não um "oi" genérico**, antes de aceitar um
modelo novo como candidato.

O `nemotron-3-ultra-550b-a55b` também reproduziu o bug nesse mesmo teste —
mas a família Nemotron da NVIDIA aceita a diretiva oficial
**`"detailed thinking off"`** para desligar o modo de raciocínio. Duas
formas testadas e confirmadas:

1. **Prefixo dentro da mensagem de usuário** (`"detailed thinking
   off\n\n" + prompt`) — funciona, mas **muda o texto do prompt**, o que
   muda a chave de cache (hash de prompt+texto) e invalida TODO o cache já
   acumulado daquele workflow. Descoberto na prática: ia trocar de
   provedor no meio de uma indexação de corpus completo (10.865 chunks já
   extraídos com sucesso, cacheados) e quase invalidei tudo isso só por
   adicionar esse prefixo ao `prompts/extract_graph.txt` — teria forçado
   reprocessar os 10.865 chunks de novo com o modelo novo, do zero.
2. **`chat_template_kwargs: {"thinking": false}` como parâmetro da API**
   (dentro de `call_args` no `settings.yaml`, repassado por
   `litellm.acompletion(**call_args)`) — **preferir sempre esta forma**:
   o texto do prompt não muda, a chave de cache continua idêntica, e o
   cache já acumulado (de qualquer modelo/prompt anterior) permanece
   válido. Validado em produção: reindexação de 7.863 documentos religada
   com este parâmetro reaproveitou os 10.865 chunks de extração já
   cacheados (de rodadas anteriores com outros modelos) e continuou o
   `summarize_descriptions` exatamente de onde tinha parado, sem nenhum
   erro, com `content` limpo e `finish_reason: stop`.

**Regra geral**: ao precisar desligar o raciocínio de um modelo em um
pipeline que já tem cache acumulado, **sempre preferir um parâmetro de API
(`chat_template_kwargs`, `reasoning_effort`, etc.) sobre editar o texto do
prompt** — o parâmetro não afeta a chave de cache, editar o prompt afeta.
Só usar o prefixo no prompt se o provedor não aceitar nenhum parâmetro
equivalente.

## `prune_graph` zera tudo em corpus pequeno — não é bug

Defaults: `min_node_freq: 2`, `min_edge_weight_pct: 40.0`,
`remove_ego_nodes: true`. Em um mini-corpus de ~10 documentos, quase toda
entidade aparece uma única vez → `Graph Pruning failed. No relationships
remain.` Isso é esperado (os limiares assumem repetição de entidades ao
longo de muitos documentos, como no corpus de produção real) — não ajustar
os limiares por causa de um teste em amostra pequena; só omitir
`prune_graph` da lista `workflows:` durante a validação em mini-corpus, e
confirmar o comportamento numa amostra de escala mais realista (algumas
centenas de documentos) antes de assumir que os limiares de produção estão
certos.
