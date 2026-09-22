# Playbook Fase 0 + Fase 2 — execução validada (2026-08-10)

Sessão real: preview estrutural (fast) + prompt-tune do corpus "Aulas e exercícios"
(disciplina Modelagem Informacional, FGV EMAp), root isolado
`/workspaces/graphrag-aulas`. Todos os comandos abaixo foram executados com
sucesso. Objetivo final do usuário: **produzir exercícios e trabalhos para
alunos de graduação**.

## 1. Root isolado e cópia do corpus (sem meta-arquivos)

```bash
mkdir -p /workspaces/graphrag-aulas/{input,prompts,cache,output,logs}
cp /workspaces/graphrag/.env /workspaces/graphrag/.gitignore /workspaces/graphrag-aulas/
cd "/workspaces/Aulas e exercícios"
find . -type f \( -name "*.md" -o -name "*.txt" -o -name "*.sql" -o -name "*.csv" \) \
  ! -name "AGENTS.md" -exec cp --parents {} /workspaces/graphrag-aulas/input/ \;
# conferir: find ... -name "AGENTS.md" | wc -l  → 0
```

## 2. settings.yaml de preview (Fase 0) — pontos críticos

- `concurrent_requests: 3` no NÍVEL RAIZ (bug de race do cache; ver
  BUGFIX-CACHE-RACE.md).
- `input.file_pattern: '^(?!.*AGENTS\.md$$).*\.(?:md|txt|text|sql|csv)$$'`
  — barra SIMPLES antes do ponto, `$$` no fim (o loader converte `$$`→`$`).
- `workflows:` no nível raiz SUBSTITUI totalmente a lista do método:
  `load_input_documents, create_base_text_units, create_final_documents,
  extract_graph_nlp, prune_graph, finalize_graph, create_communities,
  create_final_text_units` — propositalmente SEM `create_community_reports_text`
  e SEM `generate_text_embeddings` (únicas fases que custariam LLM).
- `extract_graph_nlp.text_analyzer.extractor_type: regex_english`.
- Validação ANTES de indexar:
  ```python
  from graphrag.config.load_config import load_config
  import re

  cfg = load_config(root_dir=".")
  print(repr(cfg.input.file_pattern))  # conferir \. e $ finais
  assert re.match(cfg.input.file_pattern, "sub/arquivo.md")
  assert not re.match(cfg.input.file_pattern, "sub/AGENTS.md")
  ```
- Rodada: `graphrag index --root . --method fast --verbose --skip-validation`
  (`--skip-validation`: a validação de startup testa todos os models com
  chamada real; em fast nenhum LLM é usado, então é seguro pular).

## 3. Análise da distribuição (Fase 0)

- `communities.parquet`: `level` value_counts; `describe()` de `size` por nível
  (min/mediana/max); top-10 do nível 0; contagem de `size <= 2` por nível.
- Composição por pasta: cruzar `communities.entity_ids` → `entities.text_unit_ids`
  → `text_units.document_id` → `documents.title`, mapeando título→pasta via
  varredura do filesystem de `input/` (o parquet só tem basename). Usar
  `defaultdict(list)`/dicts — ids duplicados nos parquets quebram
  `set_index`+`.loc`.
- Leitura dos sinais:
  - Sem blob: razão max/mediana do nível 0 ~1,7x (566/340 na sessão) = saudável.
  - Comunidades ≤ 2 só nos níveis-folha = atenção leve, não problema.
  - Pasta isolada: nenhuma (todas as 15 comunidades de nível 0 misturavam
    todas as pastas) = corpus bem conectado.
- regex_english em português: 16.713 entidades / 1.733.897 relações brutas em
  142 docs (2,03 MB); prune → 4.776 entidades / 300.751 relações (−71%/−83%).
  Ruído esperado; não invalida a forma.
- Entregável: relatório HTML autocontido (estilo TUTORIAL-GERAL.html) com
  config, números, tabelas por nível, composição por pasta, checklist de
  alertas e "como reproduzir".

## 4. prompt-tune (Fase 2) — objetivo final dentro do --domain

```bash
graphrag prompt-tune --root . --language Portuguese \
  --domain "<assunto + OBJETIVO FINAL, ex.: '... OBJETIVO FINAL: produzir exercícios e trabalhos para alunos de graduação — questões de múltipla escolha com gabarito e justificativas, exercícios práticos com minimundos, provas, atividades pós-aula e trabalhos, todos em português.'>" \
  --chunk-size 2400 --selection-method auto --limit 20 \
  --min-examples-required 3 --discover-entity-types --verbose
```

- Verificar a persona: `head prompts/community_report_graph.txt` deve refletir o
  objetivo (na sessão: "expert academic curriculum designer... multiple-choice
  questions with answer keys... minimundos (such as Lojas ZAGI)... in Portuguese").
- Correção obrigatória: `prompts/community_report_text.txt` (o prompt-tune nunca
  gera; gerar o default via
  `graphrag.prompts.index.community_report_text_units.COMMUNITY_REPORT_TEXT_PROMPT`).
- `grep -il "portugu" prompts/*.txt` → os 3 tunados devem conter a instrução.
- Tipos descobertos na sessão: CONCEPT, PERSON, TECHNOLOGY, DATABASE_OBJECT,
  ORGANIZATION, DOCUMENT, BUSINESS_ENTITY, PLACE, TIME_CONCEPT. Exemplos reais:
  `[PERSON] ESTUDANTE "Ator do sistema universitário que busca se inscrever em
  um major..."`, `[DATABASE_OBJECT] MODELO LÓGICO RELACIONAL`. Ruído residual
  ~1% (tipos de 1 ocorrência malformados) — o summarize+prune limpa.

## 5. Validação da extração sem completar o pipeline

Quando o index standard para no summarize (ex.: servidor local caiu), o parquet
de entidades ainda é o do preview antigo — mas as extrações LLM estão no cache:

```python
import json, pathlib, re, collections

pat = re.compile(r'\("entity"<\|>(.*?)<\|>(.*?)<\|>(.*?)\)', re.S)
tipos = collections.Counter()
exemplos = []
for f in pathlib.Path("cache/extract_graph").iterdir():
    data = json.loads(f.read_text())
    content = data["result"]["response"]["choices"][0]["message"]["content"]
    for m in pat.finditer(content):
        tipos[m.group(2).strip()] += 1
        if len(exemplos) < 12:
            exemplos.append(m.groups())
print(dict(tipos.most_common()))
```

## 6. Retomada após falha

- Rerodar o MESMO `graphrag index --root . --verbose`: o cache retoma
  (na sessão: 296/300 extract + 226/9701 summarize preservados; ~9.475 resumos
  restantes ≈ 2-3h no ritmo saudável de ~1 item/s).
- Servidor local de LLM travado (Unsloth Studio, porta 8888, `parallel_slots: 1`):
  uma geração presa monopoliza tudo → `GET /api/inference/active-generations`
  (achar o handle), `POST /api/inference/cancel`, `POST /v1/unload
  {"model_path": "<modelo>", "force_cancel_active": true}` (recarrega sozinho).
  Se nada destravar, reiniciar o app no host Windows.
- Processo morreu sem NENHUM erro nos logs (nem no `run.stdout.log`, nem no
  `logs/indexing-engine.log`) — não confundir com os dois casos acima, que
  sempre deixam rastro (mensagem de erro do LLM ou geração travada visível
  no log). Ausência total de assinatura de erro + processo realmente
  ausente em `ps aux` (não travado) = o **ambiente** (container/sessão)
  caiu por fora, não o pipeline. Caso real (`graphrag-fase2-label`,
  2026-09-21/22): rodando havia ~6h40min, morreu em
  `summarize_descriptions` 53120/114350 sem log de erro; simples rerun do
  `graphrag index` (via wrapper de retry) retomou exatamente dali via
  cache e completou o índice inteiro (114.350 descrições, todos os níveis
  de `community_reports`, embeddings) sem nenhuma falha adicional, em
  ~10h37min de runtime total. Detalhes e como blindar contra esse tipo de
  interrupção: ver armadilha 12 em `../SKILL.md`.
