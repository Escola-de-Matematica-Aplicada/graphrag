---
name: graphrag-operations
description: "GraphRAG CLI: preview, prompt-tune, validacao, cache."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [graphrag, graphrag-cli, index, prompt-tune, cache, validation, file-pattern]
    category: research
    related_skills: [graphrag-strategy, llm-wiki]
---

# Operação do GraphRAG CLI (armadilhas validadas em execução real)

## When to Use

Rodando `graphrag index` / `prompt-tune` / `query` (pacote 3.1.1) e precisando de
armadilhas de configuração, validação, cache e retomada que foram confirmadas em
execução real. Para decidir **qual fase/método/flags** usar a partir da intenção
do pesquisador, use a skill companion `graphrag-strategy` — esta skill cobre o
"como operar" (config e troubleshooting), aquela cobre o "o que rodar".

## Armadilhas validadas

1. **`file_pattern`: o loader só converte `$$`→`$` (escape de variável de
   ambiente); barras invertidas passam intactas.** Escreva `\.` (UMA barra)
   no YAML, nunca `\\.` — do contrário o regex efetivo exige barra literal e
   não casa nenhum arquivo. O valor efetivo difere do que está no arquivo
   (ex.: produção usa `\\.` + `$$` no arquivo → `\.` + `$` no config).
   **SEMPRE valide antes de rodar**:
   ```python
   from graphrag.config.load_config import load_config
   import re

   cfg = load_config(root_dir="<root>")
   print(repr(cfg.input.file_pattern))
   # conferir: bool(re.match(cfg.input.file_pattern, 'pasta/arquivo.md'))
   ```
   Exclusão de meta-arquivos por nome: `^(?!.*AGENTS\.md$$).*\.(?:md|txt|text|sql|csv)$$`
   (funciona para AGENTS.md aninhados; copiar o corpus com `find ... ! -name "AGENTS.md"` é a redundância recomendada).

2. **`graphrag index` valida TODOS os modelos configurados no startup**: envia
   uma mensagem de teste real a cada `completion_models` e `embedding_models` e
   faz `exit(1)` se QUALQUER um falhar — mesmo em `--method fast`, que não usa
   LLM nenhum. Se endpoints locais estiverem fora/instáveis e a rodada não
   chama LLM (ex.: Fase 0 fast), use `--skip-validation`; se a rodada usa LLM,
   conserte o endpoint em vez de pular.

3. **Na 3.1.1 o `summarize_descriptions` é interno ao workflow `extract_graph`**
   (não aparece como workflow separado na lista). Um erro atribuído a
   "Workflow extract_graph completed with errors" pode ser do resumo de
   descrições — o progresso aparece como
   "Summarize entity/relationship description progress: N/M". Não confunda com
   problema de prompt nem reindexe por reflexo.

4. **Cache retoma automaticamente.** Rerodar o mesmo comando `graphrag index`
   reaproveita o que já passou (chaves por fase+prompt+modelo). Nunca apague
   `cache/` por reflexo ao ver erro — primeiro verifique
   `ls cache/<fase>/ | wc -l` para saber quanto já foi preservado.

5. **Validação de extração tunada sem completar o pipeline**: o
   `cache/extract_graph/*.json` guarda a RESPOSTA BRUTA do LLM em
   `result.response.choices[0].message.content`, no formato
   `("entity"<|>TITLE<|>TYPE<|>DESC)` e `("relationship"<|>...)`. Parseie com
   `\("entity"<\|>(.*?)<\|>(.*?)<\|>(.*?)\)` (re.S) para inspecionar tipos
   descobertos e exemplos do domínio — útil quando o pipeline parou no
   summarize (o parquet de entidades ainda não foi gravado).

6. **Parquets podem ter ids duplicados** (mesma entidade em várias linhas).
   Para joins comunidades→entidades→text_units→documentos, use dicts/
   defaultdict; `set_index` + `.loc` retorna Series/DataFrame e quebra
   (`unhashable type: 'Series'`). Lembre também: `documents.parquet` guarda só o
   basename (`title`) — para distribuição por pasta, varra o filesystem.

7. **`extract_graph_nlp` (regex_english) em corpus não-inglês gera ruído
   extremo** (16,7k "entidades" frases-capitalizadas e 1,7M relações em 142
   docs; prune corta ~83%). Não invalida a avaliação de FORMA das comunidades,
   mas nunca compare contagens absolutas com extração LLM.

8. **Schema confirmado do `settings.yaml` para o pacote `graphrag` 3.1.1 instalado
   globalmente (sem venv) em 2026-08-25** — um `settings.yaml` herdado de outra versão
   pode parecer plausível (chaves como `models:`, `chunks:`, `output:`, campos de modelo
   `type: chat`/`auth_type`/`retry_strategy`) e ainda assim não bater com o schema real.
   Sempre confira com `GraphRagConfig.model_fields` (comando na armadilha 1's espírito,
   ver referência abaixo) antes de assumir. Confirmado nesta versão:
   - Top-level: `completion_models` / `embedding_models` (dicts por model_id, não
     `models:` único) + `input_storage` / `output_storage` separados de `input`/`output`.
   - `ModelConfig` (de `graphrag_llm.config.model_config`): `type` (default `litellm`),
     `model_provider`, `model`, `api_base`, `api_key`, `auth_method` (enum:
     `api_key` | `azure_managed_identity`), `call_args`, `rate_limit`, `retry`.
     `${VAR}` em `api_key` é resolvido no load — mas o validator só checa que a string é
     truthy, então um `${VAR}` não resolvido (env var ausente) passa a validação
     silenciosamente; teste uma chamada real, não só `load_config`.
   - `RateLimitConfig`: `type` (default `sliding_window`), `period_in_seconds`,
     `requests_per_period`, `tokens_per_period`.
   - `RetryConfig`: `type` (default `exponential_backoff`, alt. `immediate`),
     `max_retries`, `base_delay`, `max_delay`, `jitter`.
   - Workflows usam `completion_model_id` / `embedding_model_id` (não `model_id` genérico):
     `embed_text`, `extract_graph`, `summarize_descriptions`, `community_reports`,
     `local_search`, `global_search`, `drift_search`, `basic_search` — todos com esse
     padrão de nome.
   - Referência viva que já roda nesta máquina: `/workspaces/graphrag/settings.yaml`.

## Scripts de patch (reaplicar depois de qualquer restart de container)

**Neste fork (Escola-de-Matematica-Aplicada/graphrag) o patch já está
mergeado no código-fonte** (`packages/graphrag/graphrag/data_model/schemas.py`,
`.../index/operations/extract_graph/{graph_extractor,extract_graph}.py`,
`.../index/operations/snapshot_graphml.py`, `.../index/update/relationships.py`)
— nada a reaplicar aqui.

`scripts/apply-edge-label-patch.py` — reaplica os 5 patches (4 da armadilha 9
+ 1 achado ao portar este patch para um fork real, ver abaixo) num pacote
`graphrag` instalado via pip em site-packages (outra máquina/container sem
este checkout). Idempotente (roda seguro múltiplas vezes). Necessário depois
de todo restart de container **antes** de indexar nesses ambientes, senão
`relationships.parquet` sai sem a coluna `label` silenciosamente (sem erro).
Ver caso real de 2026-09-22 em `references/edge-label-patch.md`.

**Achado adicional (2026-09-22, ao portar o patch para o fork
Escola-de-Matematica-Aplicada/graphrag)**: `index/update/relationships.py`
(caminho de indexação incremental) tem seu **próprio** `.groupby().agg({...})`
com lista de colunas fixa, independente do de `extract_graph.py` — mesma
classe de bug da armadilha 9, em outro arquivo. Sem esse 5º patch, adicionar
`EDGE_LABEL` a `RELATIONSHIPS_FINAL_COLUMNS` quebra a atualização incremental
com `KeyError: "['label'] not in index"` (pego pelos testes unitários do
próprio pacote). Fix idêntico: agregação condicional + backfill de `""`.

**Candidato a PR upstream** (microsoft/graphrag): diffs reais prontos em
`references/patches/01-schemas.py.diff` .. `05-update_relationships.py.diff`
(comentários em inglês, já testados após reinstalação limpa do pacote e,
para o 5º arquivo, contra os testes unitários do próprio pacote) +
descrição pronta para colar num PR em `references/PR-EDGE-LABEL.md`.

## Playbook validado (receita completa)

`references/fase-0-2-playbook.md` — sessão real: root isolado, settings de
preview fast, exclusão de AGENTS.md, análise de distribuição/composição das
comunidades, prompt-tune com o objetivo final no `--domain`, correção do
`community_report_text.txt`, parse do cache e retomada após falha.

`references/vllm-local-hpc-fgv.md` — sessão real (graphrag 2.7.2 + vLLM local
no HPC FGV, A100): schema do `settings.yaml` mudou bastante entre versões do
graphrag (sempre validar `GraphRagConfig.model_fields` antes de editar um
`settings.yaml` herdado); bug real do litellm 1.97.0 (`Message is not fully
defined`, falta import, exige patch manual no site-packages); bug do
`prompt-tune` que gera `extract_graph.txt` com `})` em vez de `)` nos exemplos
few-shot (quebra `.format()` silenciosamente — a CLI mascara o erro real,
"Pipeline complete" não significa sucesso, sempre confirmar que os `*.parquet`
existem); `output/` herdado de um commit com Git LFS não resolvido vira stub
de texto e crasha o índice; XML Lattes em ISO-8859-1 são pulados em silêncio
pelo loader (só lê UTF-8). Setup dos dois servidores vLLM (completion + bge-m3
embedding) no mesmo GPU.

**Atenção: esse relato acima é do sandbox A100, não do cluster real.**
`references/v100-hpc-fgv-llamacpp.md` — cluster real HPC FGV (`hpcbo1fpr0001`
head node, fila `gpu`, nó `hpcbo1fpr0009` com **Tesla V100 32GB**, driver
CUDA 13.0). V100 = compute capability 7.0 (Volta): CUDA 13 (torch, vLLM, nvcc)
abandonou esse arch inteiro — precisa reinstalar torch/torchvision/torchaudio
como build `cu126` e, para compilar qualquer coisa em CUDA do zero (llama.cpp),
montar manualmente uma toolchain CUDA 12.6 (receita completa no arquivo:
componentes reais vêm do redistributable oficial NVIDIA, não do conda
genérico, que tem `.a`/`.so` symlinks quebrados). **vLLM 0.28.0 não tem
suporte a GGUF nenhum** (removido do pacote) — para servir `.gguf` usar
llama.cpp compilado do zero (sem binário Linux+CUDA pronto no upstream).
Armadilha cara: um stub de `libcuda.so` no `LD_LIBRARY_PATH` de runtime
mascara o driver real e derruba pra CPU silenciosamente. Resultados de
benchmark (Nemotron 3.5 Lightning MoE vs Muse-Glimmer 30B denso) e a receita
final de `settings.yaml` (completion via API cloud `agnes`, embedding via
bge-m3 local) também estão lá.

9. **Acrescentar um campo customizado a `("relationship"<|>...)` (ex.: uma
   label curta) exige patch em 4 arquivos do pacote, não só no prompt** —
   `data_model/schemas.py` (`RELATIONSHIPS_FINAL_COLUMNS`),
   `extract_graph/graph_extractor.py` (parser), e a armadilha mais traiçoeira:
   `extract_graph/extract_graph.py::_merge_relationships` faz
   `.groupby().agg(description=..., text_unit_ids=..., weight=...)` com
   lista de colunas **fixa no código**, descartando qualquer coluna nova
   silenciosamente (sem erro) ao mesclar chunks. Também:
   `snapshot_graphml.py` só inclui `weight` no `edge_attr` (por isso o
   `.graphml` sai sem `description`/`label` mesmo quando existem no
   parquet), e o GraphML writer quebra com `None`/NaN (precisa `fillna("")`
   nas colunas de texto antes de `nx.from_pandas_edgelist`). Receita
   completa com os 4 diffs e como diagnosticar em qual passo o campo se
   perde: **`references/edge-label-patch.md`**.

10. **Modelos "thinking" (ex.: `qwen35-122b-a10b`/"Agnes") são inadequados
    para extração estruturada** — gastam o `max_tokens` inteiro "pensando"
    em português e nunca emitem a tupla formatada
    (`finish_reason: "length"` no cache, `Pipeline error: Graph Extraction
    failed. No entities detected`). O mesmo bug corrompe `prompt-tune`
    (o "Thinking Process" vaza para dentro do exemplo few-shot gerado —
    detectar com `grep -c "Thinking Process" prompts/*.txt`). Trocar por
    uma variante **instruct** (`llama-4-maverick`, teto de `max_tokens:
    8192`; ou `qwen3-next-80b-a3b-instruct`) resolve. Detalhes e tabela de
    modelos observados: **`references/edge-label-patch.md`**.

11. **Cache de `extract_graph`/`summarize_descriptions` não inclui o
    modelo na chave** — trocar `completion_models` no `settings.yaml` e
    rodar de novo reaproveita silenciosamente as respostas do modelo
    anterior (sinal: rerun termina em segundos em vez de minutos). Sempre
    `rm -rf cache/` depois de trocar de modelo num root que já indexou.

12. **`graphrag index` morre no meio de uma indexação longa sem NENHUM
    erro/traceback nos logs** (nem `run.stdout.log`, nem
    `logs/indexing-engine.log`) — o progresso
    (`Summarize entity/relationship description progress: N/M`) simplesmente
    para de avançar, e `ps aux | grep "graphrag index"` não mostra mais o
    processo. Não é bug do pipeline nem falha de LLM (sem `Pipeline error`,
    sem `RateLimitError`/`ServiceUnavailableError`, sem OOM no `dmesg`,
    memória livre normal no `free -h`) — é o **ambiente** (container/sessão
    docker) que foi reiniciado/interrompido por fora, matando o processo
    junto. Diagnóstico: ausência de qualquer assinatura de erro conhecida
    (ver tabela de bugs em `graphrag-strategy`) + processo realmente
    ausente (não travado) é o sinal de causa externa, não interna.
    - **Fix confirmado**: rerodar o MESMO comando (ou o wrapper de retry) —
      o cache reaproveita tudo que já tinha rodado (armadilha 4), retomando
      exatamente do ponto interrompido. Caso real (`graphrag-fase2-label`,
      2026-09-21/22): morreu em `summarize_descriptions` 53120/114350 sem
      nenhum log de erro; ao rerodar, retomou dali, completou as 114.350
      descrições + todos os níveis de `community_reports` (5→0) +
      embeddings sem nenhuma falha, terminando em ~10h37min de runtime
      total (`output/stats.json.total_runtime`).
    - **Para sobreviver a esse tipo de interrupção** (fechamento do
      terminal/sessão, mas NÃO a um restart real do container — isso está
      fora do seu controle), rode o wrapper de retry desacoplado da
      sessão: `nohup setsid ./retry_index.sh > /dev/null 2>&1 < /dev/null &
      disown`. Só o diretório de trabalho com bind mount persistente (ex.:
      `/workspaces`) sobrevive a um restart de container; processos em RAM
      não sobrevivem.
    - **Ao montar um monitor/tail para acompanhar essa indexação**: os
      marcadores de sucesso/falha do wrapper (`SUCESSO`/`Falhou na
      tentativa`) vão para o log do wrapper (ex.: `retry.log`), não para
      `run.stdout.log`/`logs/indexing-engine.log` — inclua os três arquivos
      no `tail -F`, ou o monitor nunca vê a conclusão. E nunca use `429`/
      dígitos soltos como assinatura de rate-limit num grep de log que
      também tem barras de progresso tipo `429 / 10865` (tqdm) — casa como
      falso positivo toda vez que o contador passa por aquele número. Use a
      classe de erro completa (`litellm.RateLimitError`,
      `ServiceUnavailableError`, `Pipeline error`) como filtro.

## Servidor local de LLM (projeto FGV, porta 8888)

Geração presa (chamadas sem resposta): o servidor tem `parallel_slots: 1` — uma
geração travada monopoliza tudo. Recuperação validada: `GET
/api/inference/active-generations` (achar o handle), `POST /api/inference/cancel`,
`POST /v1/unload {"model_path": ..., "force_cancel_active": true}` (recarrega
sozinho). Se mesmo assim não gerar, reiniciar o app no host. Endpoints/estado
atual do ambiente: ver memória.
