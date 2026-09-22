# Playbook de execução ponta-a-ponta (Fases 0 → 4)

Fluxo validado em execução real (2026-08-10) num corpus acadêmico em português
(disciplina "Modelagem Informacional", 142 docs .md, objetivo: produzir
exercícios/trabalhos de graduação). Complementa as fases da SKILL.md com os
comandos exatos e os pitfalls de infra encontrados. Números citados servem de
calibração de um mini-corpus típico, não de meta.

## Regra de ouro da infra local
O pipeline de indexação do GraphRAG valida TODOS os modelos no startup
(`graphrag/index/validate_config.py`): envia uma mensagem de teste a cada
`completion_models` e `embedding_models` e dá `sys.exit(1)` se qualquer um
falhar — mesmo que a rodada não use LLM. Então:
- Rodada sem LLM (Fase 0 fast) → passe `--skip-validation`.
- Rodada com LLM e endpoint fora/errado → corrija o endpoint OU
  `--skip-validation` só se a rodada realmente não usa aquele modelo.

## Escaping do `file_pattern` (pitfall de YAML — costuma quebrar sozinho)
O valor efetivo é um regex (`re.compile`), mas o loader converte `\\`→`\` e
`$$`→`$`. No arquivo YAML escreva:
```yaml
input:
  file_pattern: '^(?!.*AGENTS\.md$$).*\.(?:md|txt|text|sql|csv)$$'
```
- `$$` vira `$` (âncora de fim) no regex.
- `\.` no YAML = `\.` no regex (dot literal).
Cuidado com ferramentas de escrita que re-escapam (JSON): ao escrever via
`write_file` o `\.` pode virar `\\.`. SEMPRE valide o valor efetivo antes de
rodar:
```python
from graphrag.config.load_config import load_config
import re

p = load_config(root_dir=".").input.file_pattern
for f in ["foo.md", "AGENTS.md", "sub/AGENTS.md"]:
    print(f, bool(re.match(p, f)))
```
O pattern `^(?!.*AGENTS\.md$).*\.(?:md|txt|text|sql|csv)$` exclui AGENTS.md
(raiz e aninhado) e ainda aceita .md/.txt/.text/.sql/.csv.

## Fase 0 — preview estrutural (zero LLM)
```bash
mkdir -p /workspaces/<root>/{input,prompts,cache,output,logs}
cp /workspaces/graphrag/.env /workspaces/graphrag/.gitignore /workspaces/<root>/
cd "<corpus>" && find . -type f \( -name "*.md" -o -name "*.txt" -o -name "*.sql" -o -name "*.csv" \) \
  ! -name "AGENTS.md" -exec cp --parents {} /workspaces/<root>/input/ \;
```
`settings.yaml`: `concurrent_requests` no nível raiz; workflows custom (sem
`create_community_reports_text` e `generate_text_embeddings`):
`load_input_documents, create_base_text_units, create_final_documents,
extract_graph_nlp, prune_graph, finalize_graph, create_communities,
create_final_text_units`; `extract_graph_nlp.text_analyzer.extractor_type:
regex_english`; `cluster_graph` (max_cluster_size 10, use_lcc true, seed fixo).
```bash
graphrag index --root . --method fast --verbose --skip-validation
```
Análise (pandas sobre `output/*.parquet` + varredura do filesystem, pois
`documents.parquet` só guarda o basename em `title`): comunidades por nível,
tamanho min/mediana/max por nível, top do nível 0 (sinal de blob se razão
max/mediana ≫ 1), nº de comunidades ≤ 2, e composição de cada comunidade do
nível 0 por pasta de origem (comunidades → entity_ids → text_units →
document_id → title → pasta via filesystem) para detectar fonte isolada.
O `regex_english` em corpus não-inglês gera muito ruído ("NOUN PHRASE" =
frases inteiras, sem descrição) — útil só para a FORMA, não para nomenclatura.

## Fase 2 — prompt-tune (sem tocar em índice)
Não depende de índice prévio (lê o input direto). Sempre em root isolado.
```bash
graphrag prompt-tune --root . --language Portuguese \
  --domain "<assunto + OBJETIVO FINAL, ex.: 'produzir exercícios e trabalhos
    para alunos de graduação — questões de MC com gabarito, minimundos, provas'>" \
  --chunk-size 2400 --selection-method auto --limit 20 \
  --min-examples-required 3 --discover-entity-types --verbose
```
- `--domain` descreve o que se quer FAZER com o conteúdo depois (molda a persona
  do `community_reports` — aqui virou "expert academic curriculum designer").
- `--selection-method auto` usa embeddings; `--discover-entity-types` descobre
  tipos do corpus.
Correção obrigatória pós-tune (o prompt-tune NUNCA cria este arquivo):
```python
from graphrag.prompts.index.community_report_text_units import (
    COMMUNITY_REPORT_TEXT_PROMPT,
)
from pathlib import Path

Path("prompts/community_report_text.txt").write_text(COMMUNITY_REPORT_TEXT_PROMPT)
```
Inspecionar antes de comprometer: persona no `community_report_graph.txt`,
tipos no `extract_graph.txt`, instrução de idioma (`grep -i portugues prompts/*`).

## Fase 3 — validação no mini-corpus (index standard)
Reescrever `settings.yaml` para o pipeline standard: `extract_graph` com
`prompt: prompts/extract_graph.txt` e SEM bloco `entity_types` manual (o
prompt-tune grava os tipos no texto do prompt; o campo manual é ignorado
quando `prompt` aponta para arquivo); `summarize_descriptions` com o prompt
tunado; `community_reports` com graph_prompt+text_prompt; `embed_text` +
`vector_store` (lancedb, vector_size = dim real do embedding, ex. 1024);
remover o bloco `workflows` (volta ao default standard).
ANTES de sobrescrever o output do preview, salve backup:
`tar czf preview_fast_output.tar.gz output/`.
```bash
graphrag index --root . --verbose
```
O cache torna a retomada incremental: se o processo morrer, rerodar o MESMO
comando continua de onde parou (extract hit 296/300, summarizes já feitos não
repetem). Backup do estado OK após sucesso.

## Fase 4 — queries de validação + material
```bash
graphrag query --root . --method local  "crie exercício de MC sobre <minimundo> com gabarito"
graphrag query --root . --method global --community-level 0 "panorama dos temas"
graphrag query --root . --method basic   "pergunta pontual"
graphrag query --root . --method drift   "pergunta de navegação conceitual"
```
Com a persona tunada, as respostas saem em formato de material didático
(enunciado, alternativas, gabarito, justificativas, em português, com citações
`[Data: ...]`). Para usar em aula, salve a resposta num `.md` e remova os
marcadores internos de citação (`[Data: Entities/Relationships/...]`).

## Incidentes de infra (LLM local, Unsloth Studio na porta 8888)
Sintoma típico: chamadas que nunca completam, timeouts, `litellm.InternalServerError`
(500), progresso do summarize parado.
1. Diagnóstico — há uma geração presa no slot único (`parallel_slots: 1`)?
   `curl -H "Authorization: Bearer $KEY" http://host.docker.internal:8888/api/inference/active-generations`
2. Cancelar: `curl -X POST -H "Authorization: Bearer $KEY" http://host.docker.internal:8888/api/inference/cancel`
   (aceita sem body; chamar 1-3x até `active` ficar vazio). Se a fila limpar mas
   a geração ainda não responder, o worker está pendurado.
3. Unload/reload do modelo: `POST /v1/unload` body
   `{"model_path":"<id>","force_cancel_active":true}`; o servidor recarrega.
4. Se continuar sem gerar: reiniciar o app no host Windows (só o usuário tem
   acesso); não derrubar com `/api/shutdown` sem permissão.
- Mensagem "The model stopped producing tokens before the response completed"
  NÃO é, necessariamente, estouro de contexto: meça o input real (aqui as
  descrições do summarize tinham ~34 tokens em média, longe de 20.480). Causa
  mais comum: modelo de reasoning sob carga travando o slot único.
- Trocar modelo local: editar `completion_models.local_completion_model.model`
  para o ID exato listado em `/v1/models` (ex. `unsloth/Muse-Glimmer-30B-GGUF`).
  O cache do summarize pode ser invalidado se o modelo entrar no hash da chave
  — não é perda grave, é a fase mais lenta de qualquer forma.
- `concurrent_requests` é GLOBAL no graphrag (raiz do settings.yaml); campo
  dentro de `completion_models`/`embedding_models` é silenciosamente ignorado.
  Subir para 4 destrava o summarize no modelo local; a Agnes continua limitada
  pelo próprio `rate_limit`.

## Números de referência (mini-corpus de 142 docs, 2 MB)
Fase 0 (regex): 16.713 entidades brutas → 4.776 pós-prune; 1,73M relações →
300.751; 887 comunidades em 7 níveis.
Fase 3 (LLM tunado): 4.391 entidades, 4.829 relações, 683 comunidades + 683
relatórios, 300 text units, 3 tabelas LanceDB 1024-dim. O grafo LLM é bem menor
e limpo (tipado, descrições em português) — a forma de distribuição se mantém.
