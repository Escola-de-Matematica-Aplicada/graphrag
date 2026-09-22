---
name: graphrag-strategy
description: "Pedidos com GraphRAG: defina fase, método, flags e bugs."
version: 1.1.0
author: Hermes
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [graphrag, knowledge-graph, rag, corpus, indexing, prompts, retrieval]
    category: research
    related_skills: [llm-wiki, grounded-citations]
  type: skill
  project: graphrag (pacote Python, testado na v3.1.1)
  companion_docs:
  - references/TUTORIAL-GERAL.html (walkthrough completo, fase por fase, com bugs)
  - references/BUGFIX-CACHE-RACE.md (causa raiz e fix da race condition de cache)
  - references/ROTEIRO-PROMPT-TUNE.md (fluxo prompt-tune sem reindex)
  - references/EXEMPLO-DRIFT-ITERACOES.md (padrão de iteração com drift)
  - references/VALIDACAO-POS-INDEX-DRIFT.md (teste de fumaça com drift após um index terminar: grounding, relação, premissa falsa, lacuna do corpus)
  - references/execucao-ponta-a-ponta.md (playbook Fases 0-4 com comandos exatos e pitfalls de infra)
  - references/BUGFIX-LITELLM-REASONING.md (fix para qwen35 retornar content como lista + reasoning misturado com JSON)
  - scripts/apply-litellm-reasoning-patch.py (reaplica o patch acima direto no litellm instalado — idempotente, necessário depois de todo restart de container)
  - references/PR-LITELLM-REASONING-CONTENT.md (candidato a PR upstream BerriAI/litellm — diff real em references/patches/01-litellm-types-utils.py.diff, comentários em inglês)
---

# Estratégia de uso do GraphRAG

## Como usar esta skill

Você (agente de IA) recebeu um pedido de um(a) pesquisador(a) que menciona
GraphRAG, um corpus de documentos, "grafo de conhecimento", indexação, ou
quer fazer perguntas sobre um conjunto de documentos já indexado. Sua
tarefa: **não execute o primeiro comando óbvio de cabeça** — primeiro
classifique a intenção usando a tabela abaixo, depois siga o procedimento
da seção correspondente. Se a intenção não estiver clara, pergunte antes de
rodar qualquer coisa que custe LLM (indexação completa é cara e não é
incremental — ver `references/BUGFIX-CACHE-RACE.md`).

## Tabela de intenção → estratégia

| O que o(a) pesquisador(a) disse (ou algo parecido) | Intenção real | Estratégia |
|---|---|---|
| "quero ver se meu corpus está bem organizado antes de indexar" / "tenho medo de indexar e ficar ruim" / "esse material tem coisa demais junta" | Avaliação estrutural pré-indexação | **Fase 0**: `graphrag index --method fast` num root isolado, sem `create_community_reports_text`/`generate_text_embeddings` — zero custo de LLM |
| "quero indexar um domínio novo/específico" / "os prompts padrão não capturam os conceitos certos" / menciona jargão técnico específico (ex.: nome de disciplina, área de pesquisa) | Prompts genéricos não servem para este domínio | **Fase 2**: `graphrag prompt-tune` num mini-corpus isolado, **antes** de tocar no índice grande |
| "quero gerar material de aula/exercícios/resumo pedagógico a partir disso" | O objetivo final não é só "responder perguntas", é produzir conteúdo derivado | `prompt-tune` com `--domain` descrevendo explicitamente o objetivo de geração (não só o assunto) + depois `query --method local` ou `drift` pedindo o formato de saída desejado |
| "rodei o index e deu erro estranho, some/volta" / erro alterna entre execuções / `FileNotFoundError` ou `TypeError ... NoneType` no cache | Bug conhecido, não erro de config | Ver **seção "Bugs conhecidos"** abaixo — não adivinhe, o fix já está mapeado |
| "quais os temas principais desse material?" / visão macro/panorama | Síntese ampla sobre todo o corpus | `query --method global --community-level 0` (nível mais macro; **nunca** rode `global` sem especificar o nível — o default pode custar centenas de chamadas) |
| pergunta específica sobre um tópico/entidade/caso | Busca pontual com contexto do grafo | `query --method local` |
| pergunta pontual simples, não precisa de relações do grafo | Busca semântica direta | `query --method basic` |
| "quero explorar/aprofundar", perguntas de acompanhamento, uso didático/socrático | Navegação iterativa no grafo | `query --method drift`, em sequência de chamadas manuais (ver "Padrão de iteração com drift") |
| "quero um diagrama/visual disso" | Comunicação, não mais dados do grafo | Rode a query certa primeiro (local/global/drift), depois produza o artefato visual a partir da resposta — não force o GraphRAG a desenhar nada |
| "isso vai custar caro/demorar muito?" | Preocupação com custo/tempo antes de comprometer | Estime pelo nº de chunks/entidades × `rate_limit` do modelo; sugira Fase 0 primeiro se ainda não foi feita |
| "quero comparar duas versões de prompt/domínio" | Iteração de config, não produção | Sempre em root isolado (mini-corpus), nunca sobrescrevendo o índice de produção |

## Procedimento por fase

### Fase 0 — Avaliação estrutural (zero custo de LLM)

Quando: **sempre que o corpus ainda não foi indexado com sucesso**, ou
mudou desde o último índice bom. Não pule esta fase para economizar tempo
— ela é gratuita e existe justamente para evitar descobrir tarde que o
corpus está mal organizado.

Ação: criar um root isolado (nunca reaproveitar o root de produção), com
`settings.yaml` customizando o campo raiz `workflows:` para incluir só até
`create_communities`/`create_final_text_units`, usando
`extract_graph_nlp` (extração local, sem LLM) em vez de `extract_graph`.
Detalhes completos e script de inspeção de distribuição:
**ver `references/TUTORIAL-GERAL.html`, seção "Fase 0"**.

Sinais de alerta a reportar ao(à) pesquisador(a): uma comunidade de nível 0
ordens de grandeza maior que as outras; muitas comunidades de tamanho 1-2;
uma fonte/pasta isolada sem ligação com o resto; alta proporção de
arquivos de meta-navegação (`AGENTS.md`, `README.md`) contaminando a
contagem.

### Fase 1 — Setup

Quando: primeira vez que esse corpus é indexado, ou não existe
`settings.yaml`/`prompts/` ainda.

Ação: montar manualmente (não confiar em `graphrag init` se não houver
TTY interativo — ele quebra com `EOFError`/"Aborted."). Gerar os prompts
padrão via Python se `prompts/` estiver ausente/incompleto. Sempre copiar
`.gitignore` junto quando copiar `.env` para um novo root. Detalhes e
snippets: **`references/TUTORIAL-GERAL.html`, seção "Fase 1"**.

### Fase 2 — Prompt-tune

Quando: o domínio do corpus é específico (jargão técnico, idioma diferente
do inglês, objetivo de uso não-genérico) — **quase sempre vale a pena**,
raramente é ok pular.

Ação **crítica**: nunca testar mudança de prompt direto no índice de
produção. `graphrag prompt-tune` não depende de nenhum índice prévio — ele
lê o input diretamente. Isolar um mini-corpus representativo (excluindo
meta-arquivos), rodar `prompt-tune` lá, inspecionar os prompts gerados, e
só então copiar para o projeto real. O campo `--domain` deve descrever não
só o assunto, mas o que o(a) pesquisador(a) quer **fazer** com o conteúdo
depois — isso muda a persona embutida no prompt de `community_reports` e
afeta diretamente a qualidade das respostas de síntese. Fluxo completo:
**`references/ROTEIRO-PROMPT-TUNE.md`**.

Correção obrigatória depois de qualquer `prompt-tune`: gerar
`community_report_text.txt` manualmente (o comando nunca cria esse
arquivo, só `community_report_graph.txt`) — ver checklist de bugs.

### Fase 3 — Indexação completa

Quando: só depois que a Fase 0 (estrutura) e a Fase 2 (prompts, se
aplicável) já foram validadas em isolamento. Este é o único passo caro e
**não incremental** — mudar um prompt depois invalida o cache inteiro
daquela fase.

Antes de rodar, confirme no `settings.yaml`:
- `concurrent_requests` está no **nível raiz** (não dentro de
  `completion_models`/`embedding_models`, onde é silenciosamente
  ignorado), com valor `3` (validado) ou `1` (garantidamente seguro,
  mais lento). O default do pacote é `25`, que **dispara uma race
  condition conhecida** no cache de arquivos — ver
  `references/BUGFIX-CACHE-RACE.md` antes de aceitar o default.
- `prompts/community_report_graph.txt` **e** `prompts/community_report_text.txt`
  existem (não só um dos dois).

Depois de terminar: salvar backup (`tar czf output_$(date +%F)_ok.tar.gz output/`
+ `cp settings.yaml settings.yaml.ok`) antes de qualquer experimento
seguinte.

### Fase 4 — Query

Quando: índice já existe e está validado.

Escolha do método pela tabela de intenção acima. Regras que não são
opcionais:
- `global` **sempre** com `--community-level 0` a menos que o(a)
  pesquisador(a) peça granularidade fina explicitamente — sem isso pode
  disparar centenas de chamadas de LLM numa única pergunta.
- `drift_search.completion_model_id` precisa ser um modelo capaz de
  retornar JSON estruturado de forma confiável — modelos locais pequenos
  falham aqui com `JSONDecodeError`. Se isso acontecer, troque o modelo,
  não o método. **Modelos "thinking"/raciocínio (ex.: `qwen35-122b-a10b`)
  também falham aqui**, pelo mesmo motivo que quebram `extract_graph` (ver
  `.agents/skills/graphrag-operations/references/edge-label-patch.md`) —
  `llama-4-maverick` validado sem erro em 3 iterações de `drift` reais.
  Além disso, `drift_search:` **não vem no `settings.yaml` por padrão** —
  se a seção não existir, copiar do template de produção antes de rodar.
- Nenhum método de query tem cache — avisar o(a) pesquisador(a) se ele(a)
  for repetir a mesma pergunta várias vezes esperando economia.

#### Padrão de iteração com `drift`

A CLI não mantém histórico entre chamadas. Para simular uma sequência de
perguntas de acompanhamento (útil para exploração/uso didático): rode uma
pergunta, leia a resposta, escolha um conceito que apareceu mas não foi
perguntado diretamente, e faça a próxima pergunta citando esse conceito
pelo nome exato usado na resposta anterior. Pare quando o sistema admitir
que não tem a informação — isso é um resultado válido (mostra o limite do
corpus), não uma falha. Exemplo completo de uma sequência real de 4
iterações: **`references/EXEMPLO-DRIFT-ITERACOES.md`**.

#### Teste de fumaça pós-indexação com `drift`

Assim que um `graphrag index` termina (todos os parquets + LanceDB
presentes), antes de liberar o índice para uso: rode uma sequência curta de
`drift` desenhada para expor problemas, não perguntas aleatórias — grounding
básico, navegação de uma relação entre duas entidades, uma pergunta com
premissa falsa embutida (testa se o sistema aluciona ou corrige com fato
real — corrigir é o resultado ideal, melhor que um simples "não sei"), e uma
pergunta plausível mas fora do escopo natural do corpus (testa se a
especulação vem rotulada, ex. `[Data: General Knowledge]`, em vez de
apresentada como fato). Roteiro completo com exemplos reais e critério de
aprovação: **`references/VALIDACAO-POS-INDEX-DRIFT.md`**.

## Playbook de execução (Fases 0 → 4)

Para executar o fluxo completo de ponta a ponta — root isolado, preview
estrutural, prompt-tune, índice standard de validação e queries de material
didático — carregue `references/execucao-ponta-a-ponta.md`
(`skill_view(file_path="references/execucao-ponta-a-ponta.md")`). Ele traz os
comandos exatos, o `file_pattern` correto com o escaping YAML, e os pitfalls de
infra (validação de modelos no startup, `--skip-validation`, recuperação do
servidor de LLM local, drift com modelo local). Use os princípios gerais abaixo
antes de qualquer execução.

## Bugs conhecidos (checar antes de investigar do zero)

| Sintoma | Causa | Fix |
|---|---|---|
| `Pipeline error: ... No such file: 'prompts/extract_graph.txt'` | `prompts/` ausente/incompleto | Gerar defaults via Python (Fase 1) |
| `FileNotFoundError`/`TypeError: ... NoneType` no cache, **inconsistente entre execuções** | Race condition: `concurrent_requests` default 25 + cache de arquivos não-atômico | `concurrent_requests: 3` (ou `1`) no nível raiz do `settings.yaml` — `references/BUGFIX-CACHE-RACE.md` tem a causa raiz completa |
| `FileNotFoundError` especificamente em `create_community_reports` | `community_report_text.txt` nunca gerado pelo `prompt-tune` | Gerar o default via Python |
| `graphrag init` trava/aborta | CLI interativa sem TTY | Montar `settings.yaml`/`prompts/` manualmente |
| `JSONDecodeError` em `drift_search` | Modelo local pequeno não retorna JSON válido no "primer" | Trocar `drift_search.completion_model_id` para modelo maior/mais confiável |
| Só uma família de embedding aparece no vector store | Overwrite entre tabelas de embedding | Confirmar tabelas/namespaces separados por família |
| Distribuição de comunidades estranha / tópico isolado | Corpus mal organizado ou poluído por meta-arquivos | Rodar Fase 0 antes de indexar de verdade |
| `graphrag index` aborta no startup validando modelos (erro 405/erro de endpoint) | `validate_config.py` testa TODOS os modelos configurados e dá exit(1) em qualquer falha | Rodada sem LLM → `--skip-validation`; rodada com LLM → corrigir endpoint (ex. `/v1`, não `/api`) |
| Chamadas ao LLM local travam / progresso do summarize parado / `InternalServerError` 500 | Geração presa no slot único (`parallel_slots:1`) do servidor local (Unsloth) | Ver `references/execucao-ponta-a-ponta.md`: active-generations → cancel → unload → trocar modelo; "stopped producing tokens" ≠ estouro de contexto |
| `graphrag index` "termina" rápido, loga `Pipeline complete`, mas `cache/extract_graph/` não cresce e nenhum `entities.parquet` aparece (exit code 1 sem traceback visível) | Bug do `prompt-tune` (graphrag 2.7.2): `extract_graph.txt` sai com `})` em vez de `)` nos exemplos few-shot, quebra `.format()` em toda chamada; a CLI mascara o erro real | Ver **`.agents/skills/graphrag-operations/references/vllm-local-hpc-fgv.md`** — sanitizar `extract_graph.txt` (`text.replace("})", ")")`) depois de todo `prompt-tune`; para achar a causa raiz de bugs mascarados assim, checar `logs/indexing-engine.log` ou chamar `graphrag.api.index.build_index(...)` direto via Python, sem a CLI |
| `pydantic.errors.PydanticUserError: 'Message' is not fully defined` em qualquer chamada de chat via litellm/graphrag | Bug real do litellm 1.97.0 (falta import de `ChatCompletionReasoningSummaryTextBlock` em `litellm/types/utils.py`) — já é a última versão do PyPI, não corrige atualizando | Patch manual no `site-packages` do venv — ver `.agents/skills/graphrag-operations/references/vllm-local-hpc-fgv.md` |
| `graphrag prompt-tune` falha em `generate_entity_types` com `ValidationError: Message` ou `JSONSchemaValidationError` | Modelo qwen35 retorna `content` como lista (reasoning blocks) e response mistura reasoning com JSON — `litellm/types/utils.py` e `json_validation_rule.py` | Patch em `litellm/types/utils.py` (Message.__init__ converte list→str) e `litellm_core_utils/json_validation_rule.py` (regex extrai JSON do response) — ver `references/BUGFIX-LITELLM-REASONING.md` |
|| Documentos somem do índice sem erro fatal, só um `WARNING ... Skipping...` no log | Arquivo em encoding diferente de UTF-8 (comum em XML Lattes, ISO-8859-1) — `input.encoding` do graphrag é um valor só para todo o corpus | Converter os arquivos ofensores para UTF-8 antes de indexar; validar o corpus inteiro decodifica em UTF-8 antes de comprometer uma rodada cara |
| `KeyError` dentro de `generate_text_embeddings` (`embedding_param_map[field]`) | `embed_text.names` no `settings.yaml` com nomes de campo do schema antigo (`text_unit_text`) em vez do atual (`text_unit.text`, com ponto) | Usar `text_unit.text`, `entity.description`, `community.full_content` — conferir com `TextEmbeddingConfig.model_fields['names'].default` |
| `graphrag index` já tinha rodado `extract_graph` com sucesso, rodou `prompt-tune` de novo e agora tudo quebra nesse mesmo workflow | `prompt-tune` não é determinístico entre execuções mesmo com `temperature: 0` — gera um `extract_graph.txt` diferente, e a chave de cache do graphrag inclui o prompt inteiro, então o cache de horas de extração fica inutilizável | **Nunca rode `prompt-tune` de novo depois que `extract_graph` já completou com sucesso.** Se faltar só `create_community_reports`/`generate_text_embeddings`, chame os `run_workflow` desses workflows direto via Python (bypassa a CLI e reaproveita os parquets já prontos) — ver `.agents/skills/graphrag-operations/references/vllm-local-hpc-fgv.md`, itens 8-9 |
| `graphrag query --method drift` falha com `ContextWindowExceededError` num grafo grande (dezenas de milhares de entidades) | O orçamento de contexto do `drift_search` escala com o tamanho da pergunta/grafo, não é fixo — visto exceder por ~1 token duas vezes seguidas (32768→28673 input; depois 49152→45057 input) mesmo após subir o limite | Não incrementar aos poucos — pular direto para um `--max-model-len` bem maior (ex. 131072, ~4x) resolve de vez; conferir a KV cache no log de startup (tende a não mudar muito entre esses valores, o gargalo é o teto por request); reiniciar com `max-model-len` diferente força um novo `torch.compile` (mais demorado quanto maior o salto) |
| Resposta de `drift` sobre "que projetos/oportunidades X poderia fazer" parece ótima mas ao pedir para detalhar um item específico o próprio modelo diz que não tem base documental | O `drift` sintetizou capacidades de pesquisadores/projetos diferentes num "projeto" único plausível mas que não existe como tal no corpus — comportamento normal de perguntas de síntese/brainstorming, não é erro | Tratar respostas desse tipo como hipóteses a validar; sempre fazer uma pergunta de acompanhamento pedindo para detalhar/citar fontes do item específico antes de usar a resposta para propor algo real |
| `extract_graph` com `finish_reason: "length"` no cache, `Pipeline error: Graph Extraction failed. No entities detected` mesmo com prompt e `entity_types` corretos | `completion_model_id` aponta para uma variante "thinking"/raciocínio (ex.: `qwen35-122b-a10b`) que gasta o `max_tokens` inteiro pensando e nunca emite a tupla formatada; subir `max_tokens` não resolve, o modelo só pensa mais | Trocar para uma variante **instruct** (`llama-4-maverick`, `qwen3-next-80b-a3b-instruct`) — ver `.agents/skills/graphrag-operations/references/edge-label-patch.md` |
| `prompt-tune` gera `prompts/*.txt` gigantes (1000+ linhas) cheios de "Thinking Process: ... Wait, I need to check..." em vez de exemplos few-shot limpos | Mesmo bug de modelo "thinking" vazando o raciocínio para dentro do exemplo gerado | Detectar com `grep -c "Thinking Process" prompts/*.txt` antes de confiar no prompt-tunado; reescrever à mão ou usar o template default do pacote em vez de remendar |
| `extract_graph` falha no fim, depois de horas de extração bem-sucedida (`litellm.RateLimitError: REQUEST_LIMIT_EXCEEDED`), e nenhum `entities.parquet`/`relationships.parquet` é escrito | O passo interno `summarize_descriptions` usa um `completion_model_id` diferente **sem `rate_limit:` configurado**, satura a cota do workspace, e a exceção derruba o workflow `extract_graph` inteiro — descartando também a extração principal, que já tinha terminado com sucesso | Configurar `rate_limit:` em **todo** modelo usado dentro de `extract_graph` (extração + summarize), não só no principal; ao reindexar, o cache do modelo principal é reaproveitado (não refaz as horas de trabalho) — só limpar `cache/summarize_descriptions/` se trocar esse modelo. Ver `.agents/skills/graphrag-operations/references/edge-label-patch.md` |
| Depois de muitas horas rodando um corpus grande, a extração vira `litellm.BadRequestError: ... hit your free daily limit. Please come back again tomorrow` (não `RateLimitError`) e o `extract_graph` falha sem escrever `entities.parquet`/`relationships.parquet`, mesmo tendo processado ~100% dos chunks | Cota **diária** (não por minuto) — **específica do workspace Databricks, tier gratuito** (não é limitação do GraphRAG/litellm nem de LLMs em geral; outros provedores como Agnes AI não exibiram isso) — esgotada. Retry/backoff não ajuda, é uma parede até o dia seguinte. A extração em si degrada por chunk (perde só as entidades daquele chunk), mas `summarize_descriptions` não tem essa proteção — a primeira chamada dele com a cota zerada derruba o workflow inteiro, descartando a extração que já tinha terminado | Trocar de **provedor** (não só de modelo) no `settings.yaml` — chamadas que falharam não são cacheadas, então religar reaproveita via cache-hit tudo que já deu certo e só refaz o que faltou, agora no provedor novo. Testar o novo endpoint com `curl` antes de religar. Ver `.agents/skills/graphrag-operations/references/edge-label-patch.md` |

**Nota (fork Escola-de-Matematica-Aplicada/graphrag, 2026-09-22):** este fork
pina `litellm==1.100.1` (`packages/graphrag-llm/pyproject.toml`), bem mais
novo que a versão em que a linha acima e `apply-litellm-reasoning-patch.py`
foram documentados (1.92.0/1.97.0). `Message.__init__` foi reescrito nessa
faixa de versões — o texto-âncora do script/diff **não bate mais** com
`litellm/types/utils.py` (confirmado rodando o script neste venv: falha limpo
com `[FALHOU] ... trecho esperado nao encontrado`, sem corromper nada). O bug
em si **continua reproduzível** mesmo em 1.100.1 — confirmado chamando
`litellm.types.utils.Message(content=[{"type": "reasoning", ...}, {"type":
"text", ...}])` diretamente, que ainda lança o mesmo
`pydantic_core.ValidationError: Input should be a valid string`. Se esse bug
aparecer de fato num modelo de raciocínio atrás deste fork, o patch precisa
ser regenerado contra o `Message.__init__` atual (que já tem campos dedicados
`reasoning_content`/`thinking_blocks`/`reasoning_items` — vale checar primeiro
se a camada de transformação de resposta do provedor específico já extrai o
bloco de raciocínio para esses campos antes de chegar em `Message(...)`, o
que tornaria o patch antigo desnecessário).

Detalhamento completo de cada um, com trechos de código-fonte que provam a
causa raiz: **`references/TUTORIAL-GERAL.html`**. Playbook ponta-a-ponta com
comandos exatos e pitfalls de infra (escaping YAML do `file_pattern`,
`--skip-validation`, recuperação do LLM local, validação de queries):
**`references/execucao-ponta-a-ponta.md`** (carregar com
`skill_view(file_path="references/execucao-ponta-a-ponta.md")`).

## Princípios gerais (aplicam em qualquer intenção)

1. **Nunca teste config/prompt novo direto no índice de produção.** Sempre
   um root isolado primeiro (preview estrutural ou mini-corpus para
   prompt-tune).
2. **Indexação completa não é incremental** depois de mudar um prompt —
   trate cada mudança de prompt como um "ou tudo ou nada" em termos de
   custo.
3. **Pergunte pelo objetivo final, não só pelo assunto**, antes de rodar
   `prompt-tune` — o objetivo (responder perguntas vs. gerar material vs.
   navegação exploratória) muda a estratégia de ponta a ponta, não só o
   `--domain`.
4. **Um resultado "não sei" ou "não encontrado no corpus" de um método de
   query é informação válida**, não um erro a ser contornado — reportar
   como tal ao(à) pesquisador(a).
5. **Custo e velocidade são o eixo de decisão entre `standard` e `fast`**,
   não qualidade pura — `fast` (via `extract_graph_nlp`) é adequado para
   avaliação estrutural, não para produção final se fidelidade semântica
   da extração importa.
