# Roteiro: prompt-tune primeiro, sem reindexar tudo

Guia para testar/ajustar prompts de indexação (`extract_graph`,
`summarize_descriptions`, `community_reports`) em um mini-corpus isolado,
**antes** de aplicar ao índice grande — evitando pagar o custo de uma
reindexação completa (~2-4h neste projeto) só para experimentar wording de
prompt ou domínio.

Pré-requisito: leia primeiro [`BUGFIX-CACHE-RACE.md`](./BUGFIX-CACHE-RACE.md).
Este roteiro já assume as correções de lá aplicadas.

## Por que isso funciona

`graphrag prompt-tune` lê os documentos de entrada **diretamente** via
`input_storage`/`input` do `settings.yaml` — o mesmo mecanismo do `index`,
mas sem passar pelo pipeline completo. Ele não depende de `output/*.parquet`
nem de `cache/` pré-existentes. Confirmação no código-fonte:
`graphrag/prompt_tune/loader/input.py::load_docs_in_chunks` chama
`create_input_reader` e faz o próprio chunking, sem tocar em nada gerado por
um índice anterior.

Isso quer dizer: dá pra rodar `prompt-tune` como primeiro comando em um root
totalmente vazio (sem `graphrag index` nunca ter rodado ali).

**Atenção**: isso vale só para os prompts de *indexação*. Uma vez que você
decide aplicar o prompt tunado e rodar `graphrag index` de verdade, qualquer
mudança de texto no prompt invalida o cache daquela fase por inteiro (o
conteúdo do prompt entra no hash da chave de cache — ver
`graphrag/cache/cache_key_creator.py`). Não tem meio-termo: ou você testa
antes num corpus pequeno (este roteiro), ou paga o reprocessamento completo
depois de mudar o prompt no índice grande.

## Passo 1 — isolar um mini-corpus representativo

Escolha uma pasta de conteúdo real e coerente com o domínio que você quer
testar (não misture assuntos). Copie só os arquivos de conteúdo de verdade —
exclua `AGENTS.md` e afins, que são meta-documentação de navegação, não
material real, e poluem a extração de entidades.

```bash
mkdir -p /workspaces/<nome-do-teste>/{input,prompts,cache,output,logs}

cd "<pasta de origem>"
find . -type f -name "*.md" ! -name "AGENTS.md" \
  -exec cp --parents {} /workspaces/<nome-do-teste>/input/ \;
```

Confira o tamanho antes de seguir — corpus pequeno (algumas centenas de KB a
poucos MB) roda o `prompt-tune` em minutos:

```bash
du -sh /workspaces/<nome-do-teste>/input
find /workspaces/<nome-do-teste>/input -type f | wc -l
```

## Passo 2 — copiar `.env` e `.gitignore`

```bash
cp /workspaces/graphrag/.env /workspaces/<nome-do-teste>/.env
cp /workspaces/graphrag/.gitignore /workspaces/<nome-do-teste>/.gitignore   # sempre junto do .env
```

## Passo 3 — `settings.yaml` do root de teste

Reaproveite a config validada do índice principal (`/workspaces/graphrag/settings.yaml`),
com estas diferenças:

- `concurrent_requests: 3` no topo — **obrigatório**, é a correção do bug de
  race condition documentado em `BUGFIX-CACHE-RACE.md`. Sem isso o índice
  completo (passo 6) pode voltar a falhar de forma não-determinística.
- `input_storage.base_dir: "input"` (local ao root de teste) e
  `input.file_pattern: '.*\.md$$'` — mais simples que o padrão do índice
  principal, já que a pasta só tem o que interessa.
- `extract_graph:` **sem** o campo `entity_types:` manual — ele é ignorado
  assim que existe um `prompt:` apontando pra arquivo (o `prompt-tune` grava
  a lista de tipos direto no texto do prompt).
- Mantenha os mesmos `completion_models`/`embedding_models` (Agnes +
  modelo local), `drift_search.completion_model_id: default_completion_model`
  (não use `local_completion_model` no drift — modelo pequeno falha ao gerar
  JSON válido no estágio "primer", ver `BUGFIX-CACHE-RACE.md`).

## Passo 4 — rodar o `prompt-tune` (sem index, sem cache prévio)

```bash
cd /workspaces/<nome-do-teste>
graphrag prompt-tune \
  --root . \
  --language Portuguese \
  --domain "<descrição do domínio + objetivo final, ex.: 'gerar material de aula e exercícios sobre X'>" \
  --chunk-size 2400 \
  --selection-method auto \
  --limit 20 \
  --min-examples-required 3 \
  --discover-entity-types \
  --verbose
```

Notas sobre as flags:
- `--domain` deve descrever não só o assunto, mas **o que você quer fazer
  com o conteúdo depois** (ex.: "extrair conceitos e exercícios para permitir
  a criação de novo material de aula") — isso molda a persona e o objetivo
  embutidos no prompt de `community_reports`, que é o que mais influencia a
  qualidade das respostas de síntese depois.
- `--discover-entity-types` deixa o modelo descobrir tipos de entidade
  específicos do corpus (ex.: `distributed_file_system`, `algorithm`,
  `architecture_pattern`) em vez de reusar uma lista genérica — normalmente
  produz extração mais relevante que tipos fixos como `organization, person,
  geo, event`.
- `--selection-method auto` usa embeddings pra escolher chunks representativos
  do corpus inteiro (em vez de `random` ou os primeiros `top`).

Resultado esperado em `prompts/` (poucos minutos, corpus pequeno):
- `extract_graph.txt`
- `summarize_descriptions.txt`
- `community_report_graph.txt`

## Passo 5 — corrigir o arquivo que o `prompt-tune` NÃO gera

O `prompt-tune` nunca cria `community_report_text.txt` (só o `graph_prompt`),
mas `settings.yaml` referencia os dois (`graph_prompt` e `text_prompt`). Sem
isso, o `create_community_reports` quebra com `FileNotFoundError` na fase de
resumo por texto. Gere o padrão de fábrica pra esse arquivo:

```bash
python3 -c "
from graphrag.prompts.index.community_report_text_units import COMMUNITY_REPORT_TEXT_PROMPT
from pathlib import Path
Path('prompts/community_report_text.txt').write_text(COMMUNITY_REPORT_TEXT_PROMPT)
"
```

## Passo 6 — inspecionar antes de comprometer

Leia os 3-4 arquivos gerados em `prompts/` e confira:
- Os tipos de entidade fazem sentido pro domínio?
- A persona/objetivo em `community_report_graph.txt` reflete o que você quer
  (ex.: "enabling creation of new teaching materials")?
- A instrução de idioma está presente (`grep -i portuguese prompts/*.txt`)?

Se não gostar, ajuste `--domain`, `--limit` (mais chunks = mais contexto pro
gerador de prompt) ou `--min-examples-required` e rode de novo — ainda sem
custo de indexação completa.

## Passo 7 — só agora, indexar o mini-corpus

```bash
nohup graphrag index --root . --verbose > index.log 2>&1 &
```

Corpus pequeno (dezenas de arquivos, menos de 1-2MB) deve terminar em minutos,
não horas. Monitore como de costume:

```bash
tail -f logs/indexing-engine.log | grep -E "(Workflow|progress|ERROR)"
```

Se aparecer `FileNotFoundError`/`TypeError: ... NoneType` durante
`extract_graph` ou `summarize_descriptions`, a causa é a mesma race condition
documentada em `BUGFIX-CACHE-RACE.md` — confirme que `concurrent_requests: 3`
(ou `1`, mais lento porém garantidamente seguro) está no nível raiz do
`settings.yaml` desse root de teste.

## Passo 8 — testar os 4 métodos de query

```bash
graphrag query --root . --method local  "<pergunta pedindo material de aula/exercício>"
graphrag query --root . --method global --community-level 0 "<pergunta ampla sobre temas>"
graphrag query --root . --method basic  "<pergunta pontual>"
graphrag query --root . --method drift  "<pergunta que precisa navegar o grafo>"
```

Se `drift` falhar com `JSONDecodeError`/`Expecting value: line 1 column 1`,
confirme que `drift_search.completion_model_id` está em
`default_completion_model` (Agnes), não no modelo local — ver
`BUGFIX-CACHE-RACE.md`.

## Passo 9 — só depois de validado, aplicar ao índice grande

Copie os prompts validados (`prompts/*.txt`) e o `--domain` que funcionou
para o `settings.yaml`/`prompts/` do índice principal, e só então rode a
reindexação completa — agora sabendo que o resultado vai ser bom, sem gastar
horas às cegas.
