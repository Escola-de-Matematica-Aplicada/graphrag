# vLLM local no HPC FGV (A100) como backend do GraphRAG — armadilhas validadas (2026-08-21)

Sessão real: `graphrag-full-setores` (root do projeto), graphrag **2.7.2**, vLLM **0.19.1**,
modelos locais `cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit` (completion, MTP) e
`BAAI/bge-m3` (embedding), servidos via `vllm serve` no nó GPU do HPC FGV. Detalhes de infra
(driver/CUDA, filas PBS, por que `vllm==0.19.1` e não a última) estão em `AGENT-MEMORY.md`
na raiz do repo do projeto — leia aquele arquivo primeiro para o contexto de ambiente.

## Setup dos dois servidores vLLM no mesmo GPU

Completion primeiro, embedding depois (a fração de `--gpu-memory-utilization` é sobre o
total da GPU, não sobre o que resta — se inverter a ordem ou não deixar margem, o segundo
processo pode falhar por falta de memória livre):

```bash
vllm serve <dir-modelo-completion> --served-model-name qwen3-next-80b-awq --port 8000 \
  --gpu-memory-utilization 0.75 --max-model-len 32768 \
  --speculative-config '{"method": "mtp", "num_speculative_tokens": 3}'

vllm serve <dir-modelo-embedding> --served-model-name bge-m3 --port 8001 \
  --runner pooling --gpu-memory-utilization 0.08
```

Ver `hpc/run_fase3_full_index.sh` no repo do projeto para o script completo (sobe os dois,
espera `/health`, faz smoke test real de `/v1/chat/completions` e `/v1/embeddings` antes de
confiar nos endpoints).

## Bugs reais encontrados (graphrag 2.7.2 + litellm 1.97.0 + vLLM local)

1. **litellm 1.97.0 quebra em QUALQUER chamada de chat**:
   `pydantic.errors.PydanticUserError: 'Message' is not fully defined; ... define
   ChatCompletionReasoningSummaryTextBlock, then call Message.model_rebuild()`.
   Causa raiz confirmada lendo o source: `litellm/types/utils.py` usa o tipo
   `ChatCompletionReasoningSummaryTextBlock` (definido em `litellm/types/llms/openai.py`)
   mas **não o importa** — falta na lista `from .llms.openai import (...)`. Reproduz direto
   com `litellm.completion(...)`, sem graphrag no meio. Confirmado que `1.97.0` é a última
   versão no PyPI nesta data (`pip index versions litellm`) — **não dá para corrigir
   atualizando**. Fix aplicado (local, no `site-packages` do venv): editar
   `litellm/types/utils.py` e adicionar `ChatCompletionReasoningSummaryTextBlock` no bloco de
   import de `.llms.openai`. Refazer esse patch se o `.venv` for recriado do zero.

2. **`max_tokens` alto demais quebra com prompt grande**: `max_tokens: 16384` no `models:`
   do `settings.yaml` + um prompt de ~16k tokens de input excede o `--max-model-len 32768`
   do vLLM → `litellm.ContextWindowExceededError`. Fix: usar algo como `max_tokens: 4096`
   (deixa margem de sobra para o input; extração de entidades/resumos não precisa de saídas
   enormes).

3. **`graphrag prompt-tune` (2.7.2) gera `prompts/extract_graph.txt` com bug nos poucos-tiros
   (few-shot)**: toda linha de entidade/relação nos exemplos termina em `})` (chave `}`
   espúria) em vez de só `)`. Isso quebra `self._extraction_prompt.format(...)` dentro do
   `GraphExtractor` com `ValueError: Single '}' encountered in format string` — **em toda
   chamada de extração, para todo chunk**. Sintoma visível (enganoso): `graphrag index`
   termina rápido demais, `cache/extract_graph/` não cresce, nenhum `entities.parquet` é
   gerado, e a CLI só reporta `sys.exit(1)` sem imprimir a causa (ver bug #4). Fix
   obrigatório depois de QUALQUER `prompt-tune`, antes de rodar `graphrag index`:
   ```python
   from pathlib import Path

   p = Path("prompts/extract_graph.txt")
   text = p.read_text(encoding="utf-8")
   p.write_text(text.replace("})", ")"), encoding="utf-8")
   ```
   Confirmar que `.format(tuple_delimiter=..., record_delimiter=..., completion_delimiter=...,
   input_text=..., entity_types=...)` não quebra mais antes de comprometer a rodada cara.
   Ver o step automatizado em `hpc/run_fase3_full_index.sh`. Só `extract_graph.txt` tem esse
   bug — `community_report_graph.txt`, `summarize_descriptions.txt` e
   `community_report_text.txt` saem limpos do prompt-tune.

4. **A CLI do `graphrag index` mascara erros reais de workflow.** `graphrag/cli/index.py`
   decide `sys.exit(1)` só checando `output.errors` de cada `PipelineRunResult`, e a
   mensagem de resumo (`"Errors occurred during the pipeline run..."` ou `"All workflows
   completed successfully."`) pode nunca aparecer no log capturado (motivo exato não
   totalmente isolado — pode ser buffering, pode ser outro bug de logging na mesma família
   do `--dry-run`, ver abaixo). **Não confie em "Pipeline complete" no log combinado
   (stdout+stderr) como sinal de sucesso** — sempre cheque se os `*.parquet` esperados
   (`entities.parquet`, `relationships.parquet`, `communities.parquet`,
   `community_reports.parquet`) existem de verdade em `output/`. Para achar a causa raiz de
   verdade quando isso acontece: (a) primeiro cheque `logs/indexing-engine.log` — a CLI
   sempre grava o traceback completo lá mesmo quando o stdout/stderr não mostra nada
   (`grep -n "Traceback\|Error" logs/indexing-engine.log`); (b) se ainda não for suficiente,
   bypassar a CLI chamando `graphrag.api.index.build_index(config=..., verbose=True)` direto
   via um script Python pequeno, e imprimir `output.errors` de cada `PipelineRunResult` —
   isso reproduz o erro real sem a camada de CLI no meio.

5. **`output/*.parquet`, `output/*.graphml`, `output/context.json` herdados de um commit git
   com Git LFS** (não instalado neste ambiente — `which git-lfs` vazio) são só *stubs* de
   texto (~130 bytes, começam com `version https://git-lfs.github.com/spec/v1`). Se
   `output/` ainda tiver esses stubs de uma versão anterior do projeto, `graphrag index`
   crasha tentando ler `context.json` como JSON de verdade
   (`JSONDecodeError: Expecting value: line 1 column 1`). Fix: `rm -rf output/*` antes de
   indexar se o projeto foi clonado de um repo com filtros LFS não resolvidos. Checar
   `git lfs status` / `which git-lfs` e o tamanho dos arquivos (~130 bytes = stub) antes de
   assumir que é um bug de config.

6. **XML/CSV com encoding Latin-1 (ISO-8859-1) são pulados silenciosamente.** O loader de
   input do graphrag só lê UTF-8 por padrão (`input.encoding`, default `utf-8`, um valor só
   para todo o corpus — não dá para misturar encodings por arquivo). Currículos Lattes
   (`.xml`) exportados do sistema Lattes CNPq costumam vir em ISO-8859-1
   (`<?xml ... encoding="ISO-8859-1"?>`), e o loader emite só um `WARNING` e segue sem esses
   documentos — não é um erro fatal, mas silenciosamente derruba parte do corpus
   (justamente os currículos, se o objetivo envolver mapear capacidades de pesquisadores).
   Sinal de alerta: `grep -i "Skipping" logs/prompt-tuning.log` ou `logs/indexing-engine.log`.
   Fix: converter para UTF-8 antes de indexar —
   ```python
   for f in pasta.glob("*.xml"):
       raw = f.read_bytes()
       try:
           raw.decode("utf-8")
           continue
       except UnicodeDecodeError:
           text = raw.decode("iso-8859-1")
       text = text.replace('encoding="ISO-8859-1"', 'encoding="UTF-8"')
       f.write_text(text, encoding="utf-8")
   ```
   Depois, validar o corpus inteiro (`for f in base.rglob("*"): f.read_text(encoding="utf-8")`)
   antes de comprometer uma rodada cara.

## Schema do `settings.yaml` mudou bastante entre versões do graphrag

O `settings.yaml` herdado deste projeto tinha sido escrito para uma versão **mais antiga**
do graphrag (`type: litellm`, `completion_models:`/`embedding_models:` separados,
`concurrent_requests` na raiz, `input_storage:`/`output_storage:` como chaves top-level).
**Nenhum desses nomes existe no 2.7.2** — carregar esse arquivo com `load_config` não dava
erro (campos desconhecidos são ignorados silenciosamente pelo pydantic), só resultava num
`models: {}` vazio e comportamento default, sem avisar que a config real não tinha efeito
nenhum. **Nunca assuma o schema de um `settings.yaml` herdado ou de um skill/doc antigo**
sem validar contra a versão instalada:

```python
from graphrag.config.models.graph_rag_config import GraphRagConfig

print(list(GraphRagConfig.model_fields))
```

Ainda melhor: gerar um `settings.yaml` de referência fresco (`graphrag init --root
<dir-temporario>`) e comparar campo a campo antes de editar um `settings.yaml` herdado.
Note: `graphrag init --root .` funcionou **sem TTY** no 2.7.2 (ao contrário do relatado para
`3.1.1` em `TUTORIAL-GERAL.html`, que trava/aborta com `EOFError` sem TTY interativo) — o
comportamento mudou entre versões, então teste antes de assumir.

Diffs confirmados 2.7.2 vs. o `settings.yaml` antigo deste projeto:

| Antigo (schema desconhecido/versão anterior) | 2.7.2 |
|---|---|
| `completion_models:` / `embedding_models:` (dois blocos) | `models:` (um dict só, chaveado por nome) |
| `type: litellm`, `model_provider: openai` | `type: chat` ou `type: embedding`, `model_provider: openai` (`api_base` custom funciona pra qualquer endpoint OpenAI-compatível, inclusive vLLM local) |
| `concurrent_requests` na raiz do `settings.yaml` | campo **por modelo**, dentro de `models.<nome>:` — não existe mais na raiz do `GraphRagConfig` |
| `input_storage: {type, base_dir}` (top-level) | `input.storage: {type, base_dir}` (aninhado dentro de `input:`) |
| `output_storage: {...}` / `cache: {storage: {...}}` | `output: {type, base_dir}` / `cache: {type, base_dir}` (sem aninhamento extra) |
| `vector_store: {type, db_uri, vector_size}` (bloco único) | `vector_store: {<nome>: {type, db_uri, container_name}}` (dict chaveado por nome) |
| `call_args: {temperature, max_tokens}` aninhado no modelo | campos `temperature`/`max_tokens` direto no nível do modelo (sem `call_args:`) |

Outros pontos confirmados no 2.7.2:
- `graphrag prompt-tune` gera sozinho `extract_graph.txt`, `summarize_descriptions.txt` **e**
  `community_report_graph.txt` (3 arquivos) — mas **ainda não** gera
  `community_report_text.txt` (mesma lacuna documentada para `3.1.1` em
  `ROTEIRO-PROMPT-TUNE.md`). O fix python continua igual:
  ```python
  from graphrag.prompts.index.community_report_text_units import (
      COMMUNITY_REPORT_TEXT_PROMPT,
  )
  from pathlib import Path

  Path("prompts/community_report_text.txt").write_text(COMMUNITY_REPORT_TEXT_PROMPT)
  ```
- `graphrag query` exige `--query`/`-q` explícito — **não** aceita a pergunta como argumento
  posicional (`graphrag query --root . --method local --query "..."`, não
  `graphrag query --root . --method local "..."`).
- `graphrag index --dry-run` tem um bug de logging cosmético
  (`logger.info("Dry run complete, exiting...", True)` → `TypeError` dentro do módulo
  `logging`), mas o dry-run em si passa (`exit 0`) — é ruído, não um erro de config real.
- Workflows do pipeline `standard` no 2.7.2: `load_input_documents`, `create_base_text_units`,
  `create_final_documents`, `extract_graph`, `finalize_graph`, `extract_covariates`,
  `create_communities`, `create_final_text_units`, `create_community_reports`,
  `generate_text_embeddings`. Não há um workflow `summarize_descriptions` separado — está
  embutido em `extract_graph` (mesmo padrão já documentado para `3.1.1` em
  `graphrag-operations/SKILL.md`, item 3 das armadilhas).

## Resultado da Fase 3 completa em `graphrag-full-setores` (2026-08-21/22)

Rodou até o fim com sucesso, em **13 jobs PBS** ao longo de ~24h corridas (~20h30 de GPU
efetiva; extract_graph sozinho levou ~9h mesmo com A100 + MTP, um job bateu o walltime de
12h por 96 segundos): 124 documentos, 3.899 chunks, **25.449 entidades, 45.408 relações,
4.991 comunidades (7 níveis), 4.981 relatórios de comunidade** (10 falharam por erro de
parsing JSON pontual do LLM — normal, ~0,2%, não é bug de config) e as 3 tabelas de
embedding no LanceDB. Relatório completo do processo, com linha do tempo exata de cada job
(via `qstat -xf`) e o catálogo dos 9 bugs reais encontrados:
`graphrag/relatorio-fase3-vllm-local-2026-08-21.html` (repo do projeto).

Queries de validação: `local`, `global` e `basic` funcionaram muito bem (respostas ricas,
citando entidades/fontes corretamente); `drift` falhou de início com
`ContextWindowExceededError` — o grafo é grande (25k entidades) e os sub-orçamentos de
contexto do `drift_search` (`data_max_tokens`/`local_search_max_data_tokens`/
`primer_llm_max_tokens`, ~12000 cada) geraram uma chamada real com **28.673 tokens de
entrada**; com `max_tokens: 4096` de saída, o total (32.769) excedeu o
`--max-model-len 32768` do vLLM **por exatamente 1 token**.

**Fix (primeira tentativa, só parcialmente resolvido)**: subir para `--max-model-len 49152`
(e `--gpu-memory-utilization 0.80`) resolveu a primeira pergunta de `drift` — mas numa
pergunta de acompanhamento (mais complexa, pedindo aprofundamento de um item específico), o
mesmo padrão se repetiu: nova chamada real com **45.057 tokens de entrada** (91,7% do
limite), excedendo de novo `--max-model-len 49152` por ~1 token. Ou seja, **o orçamento de
contexto do `drift_search` escala proporcionalmente ao `--max-model-len` configurado, não é
fixo** — subir o limite um pouco só adia o problema para a próxima pergunta mais complexa.
**Fix definitivo**: pular direto para um valor bem maior (`--max-model-len 131072`, ~4x o
original) em vez de subir aos poucos. A partir daí, tanto a pergunta original quanto
perguntas de acompanhamento bem mais longas (pedindo arquitetura técnica + roteiro de
implementação + riscos + editais, tudo numa única pergunta) rodaram sem erro. A KV cache
disponível não mudou muito entre 32768/49152/131072 nesse setup (~14 GiB / ~144k tokens) —
o gargalo real é o teto de contexto por request, não a memória da GPU. Trocar
`--max-model-len` invalida o cache de `torch.compile` (compilado por tamanho de contexto),
adicionando ~90s a ~5min ao startup do servidor na primeira vez com um valor novo (quanto
maior o salto, mais demora a compilar).

Com esse fix, `drift` produziu análises de altíssima qualidade em múltiplas rodadas: (a) 7
oportunidades de projeto concretas para a FGV EMAp como um todo, cada uma com empresas-alvo
e pesquisador nomeado; (b) 5 tipos de projeto específicos para um pesquisador nomeado
(cruzando métodos, publicações e parcerias existentes dele); (c) aprofundamento técnico de
um desses projetos (arquitetura, pesquisadores por componente, roteiro de implementação em
fases, riscos técnicos/regulatórios, editais nominais).

**Achado importante sobre confiabilidade do `drift` para perguntas de síntese/oportunidade**:
numa pergunta ampla ("que projetos o pesquisador X poderia desenvolver"), o `drift` combinou
capacidades de pesquisadores **diferentes** num "projeto" único, coerente e bem escrito, mas
que não corresponde a nenhum projeto documentado de fato — uma síntese plausível, não um
fato do corpus. Ao pedir para **aprofundar** esse item específico numa pergunta de
acompanhamento (mais contexto disponível, foco mais estreito), o próprio `drift` se
autocorrigiu e declarou explicitamente: *"a descrição detalhada solicitada não pode ser
fornecida com base nos dados fornecidos"*, separando o que é documentado (projetos reais de
cada pesquisador, isolados) do que era síntese hipotética da resposta anterior. **Lição
prática**: tratar respostas de `drift` do tipo "oportunidades"/"o que X poderia fazer" como
hipóteses de brainstorming a validar, não fatos — sempre fazer uma pergunta de
acompanhamento pedindo para **detalhar/citar fontes** do item específico antes de usar a
resposta para propor algo a um pesquisador ou empresa real.

Duas armadilhas adicionais encontradas ao terminar a indexação:

7. **`embed_text.names` usa notação com ponto, não underscore.** O `settings.yaml` antigo
   (schema de versão anterior) usava `text_unit_text`, `entity_description`,
   `community_full_content` — no 2.7.2 os nomes válidos são `text_unit.text`,
   `entity.description`, `community.full_content` (confirmar sempre com
   `TextEmbeddingConfig.model_fields['names'].default`). Nome errado não dá erro de
   schema/pydantic (aceita qualquer string na lista) — só quebra depois, dentro do workflow,
   com `KeyError` no `embedding_param_map[field]`.

8. **`graphrag prompt-tune` NÃO é determinístico entre execuções** mesmo com
   `temperature: 0` no modelo — a seleção de chunks (`--selection-method auto`) e a geração
   das poucas-tiros variam o suficiente para produzir um `extract_graph.txt` DIFERENTE a
   cada rodada, inclusive reintroduzindo o bug do item 3 em posições diferentes. Como o cache
   do graphrag usa o **conteúdo completo do prompt formatado** como parte da chave (`get_cache_key`
   em `graphrag/language_model/providers/litellm/get_cache_key.py` hasheia `messages`/`input`
   inteiros), **rodar `prompt-tune` de novo depois que `extract_graph` já rodou com sucesso
   invalida TODO o cache de extração** (custou ~9h numa rodada real) — não tem como recuperar
   o prompt exato de antes a partir do cache (ele só guarda a resposta, não o request).
   **Regra prática: depois que `extract_graph`/`summarize_descriptions`/`create_communities`
   já completaram com sucesso (confira `output/entities.parquet` etc. existem), NUNCA rode
   `prompt-tune` de novo nem `graphrag index` do zero — só rode os workflows que faltam.**

9. **Como rodar só os workflows restantes sem reprocessar o que já terminou**: chamar as
   funções `run_workflow` de cada workflow direto via Python, com um `PipelineRunContext`
   apontando pro `output/` e `cache/` já existentes — bypassa a necessidade de reprocessar
   `extract_graph` (que exigiria os prompts, sujeitos ao bug do item 8) e evita rodar o
   pipeline `standard` inteiro de novo:
   ```python
   from pathlib import Path
   from graphrag.config.load_config import load_config
   from graphrag.index.run.utils import create_run_context
   from graphrag.index.workflows.create_community_reports import run_workflow as run_ccr
   from graphrag.index.workflows.generate_text_embeddings import run_workflow as run_gte
   from graphrag.utils.api import create_cache_from_config, create_storage_from_config

   config = load_config(root_dir=Path("graphrag-full-setores"))
   output_storage = create_storage_from_config(config.output)
   cache = create_cache_from_config(config.cache, config.root_dir)
   context = create_run_context(output_storage=output_storage, cache=cache)
   result = await run_ccr(
       config, context
   )  # le entities/relationships/communities do output_storage
   result2 = await run_gte(config, context)  # le community_reports que acabou de escrever
   ```
   Cada `run_workflow` retorna um `WorkflowFunctionOutput` (só tem `.result` e `.stop` —
   **não tem `.errors`**, ao contrário do `PipelineRunResult` que a CLI usa; não confundir os
   dois tipos). Ver `hpc/run_remaining_workflows.py` no repo do projeto para o script completo
   validado (rodou `create_community_reports` com sucesso reaproveitando 100% do
   `entities.parquet`/`relationships.parquet`/`communities.parquet` de uma rodada anterior).

## Setup de projeto (venvs, modelos, filas PBS)

Detalhes completos (por que `vllm==0.19.1` e não a última, driver/CUDA do host, filas PBS
`workq`/`a100`, layout de `.venv`/`.venv-vllm`/`.models`) estão em `AGENT-MEMORY.md` na raiz
do repo do projeto (`~/Escola-de-Matematica-Aplicada/matematica-na-industria/AGENT-MEMORY.md`)
— leia aquele arquivo para o contexto de ambiente antes de repetir a investigação.
