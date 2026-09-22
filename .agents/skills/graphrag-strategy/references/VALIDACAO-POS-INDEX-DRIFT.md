# Teste de fumaça pós-indexação com `drift`

Complementa `EXEMPLO-DRIFT-ITERACOES.md` (que é sobre uso didático/
exploratório). Este documento é sobre **validação**: depois que um `graphrag
index` termina (parquets + LanceDB todos presentes), rode uma sequência curta
de perguntas `drift` desenhadas para expor problemas específicos antes de
liberar o índice para uso real — não perguntas aleatórias sobre o conteúdo.

## Por que `drift` para isso (e não `local`/`global`)

`drift` já faz múltiplos saltos internos no grafo (primer + follow-ups) numa
única chamada, então cada pergunta testa: embedding + busca vetorial +
navegação de relações + geração de resposta grounded — a cadeia inteira do
pipeline de query, não só um pedaço.

## A sequência (5 perguntas, cada uma testando uma coisa diferente)

Escolha uma entidade central do corpus (uma pessoa/conceito bem documentado)
como âncora. Sessão real de validação (`graphrag-fase2-label`, corpus DHBB —
dicionário biográfico de políticos brasileiros, entity types
`pessoa`/`partido`/`ideologia`, âncora: Eduardo Campos):

### 1 — Grounding básico

```bash
graphrag query --root . --method drift \
  "Quem foi Eduardo Campos e qual foi sua trajetória política e partidária?"
```

O que checar: a resposta cita `[Data: Sources (N)]` de forma consistente, os
fatos batem com o que se sabe do domínio, e a resposta revela conceitos
relacionados que não foram pedidos explicitamente (sinal de que o grafo tem
profundidade ali — vira gancho para a pergunta 2).

### 2 — Puxar o fio de um conceito revelado

```bash
graphrag query --root . --method drift \
  "Como foi a migração de Eduardo Campos do PMDB para o PSB, e que \
  posicionamento ideológico essa mudança de partido representou?"
```

O que checar: consistência com a resposta 1 (mesmas fontes, sem contradição),
e — se o corpus tiver um entity type customizado (aqui, `ideologia`) — se ele
está sendo de fato usado no raciocínio, não só extraído e ignorado na hora de
responder.

### 3 — Navegação de relação entre entidades

```bash
graphrag query --root . --method drift \
  "Qual foi a relação política entre Eduardo Campos e seu avô Miguel \
  Arraes, e como o legado de Arraes influenciou a carreira de Campos?"
```

O que checar: o `drift` precisa atravessar uma relação pessoa↔pessoa
(não só descrever uma entidade isolada) para responder — testa se o grafo de
relações está bem formado, não só os nós.

### 4 — Armadilha de premissa falsa (teste de honestidade, forte)

Construa uma pergunta que pareça plausível mas embuta um fato impossível —
idealmente algo que o próprio corpus contradiz diretamente (não um detalhe
obscuro, um fato central e verificável). Exemplo real:

```bash
graphrag query --root . --method drift \
  "Qual foi o percentual exato de votos que Eduardo Campos obteve no \
  primeiro turno das eleições presidenciais de 2014, e como isso se \
  comparou à votação de Aécio Neves e Dilma Rousseff nesse turno?"
```

A armadilha: Eduardo Campos morreu em 13/08/2014, antes do primeiro turno
(05/10/2014) — não pode ter recebido votos. Um sistema que aluciona inventa
um percentual. **Resultado ideal** (obtido na sessão real): o sistema recusa
a premissa, explica a morte antes da eleição, identifica quem assumiu a
candidatura (Marina Silva) e cita os percentuais reais dos outros candidatos
com fonte (`[Data: Sources (9525)]`) — corrigir a pergunta com fato real é
um resultado **melhor** que um simples "não sei", porque prova que o sistema
prioriza o corpus sobre o padrão sintático da pergunta.

O que checar: se a resposta inventa um número para a entidade impossível,
**é alucinação real** — investigar o prompt de `community_reports`/`drift`
ou trocar de modelo (ver armadilha 10 em `graphrag-operations/SKILL.md`)
antes de liberar o índice.

### 5 — Admissão de lacuna do corpus

```bash
graphrag query --root . --method drift \
  "As políticas sociais de Eduardo Campos como governador, como o Pacto \
  pela Vida e o Mãe Coruja Pernambucana, foram formalmente inspiradas ou \
  desenvolvidas em parceria com organismos internacionais como o PNUD ou \
  o Banco Mundial?"
```

Escolha algo plausível mas fora do escopo natural do corpus (aqui: um
dicionário biográfico dificilmente documenta parcerias institucionais
formais em detalhe). **Resultado ideal**: a resposta admite explicitamente
a ausência de evidência direta ("não há menção explícita... nos registros
disponíveis") e, se especular além disso, **rotula a especulação**
separadamente com uma tag do tipo `[Data: General Knowledge]` em vez de
apresentar tudo como fato indistinto do corpus — essa etiqueta é o sinal de
que o sistema está separando corretamente "o que o grafo sustenta" de
"o que o modelo está inferindo por conta própria".

## Critério de aprovação do índice

| Teste | Falha = | 
|---|---|
| 1-2 (grounding) | Resposta sem citação `[Data: Sources...]`, ou fatos que contradizem o corpus conhecido |
| 3 (relação) | Resposta trata as duas entidades como não relacionadas, ou não menciona a relação central que deveria existir |
| 4 (premissa falsa) | Inventa um número/fato para a entidade/situação impossível, sem corrigir a premissa |
| 5 (lacuna) | Apresenta especulação como fato do corpus, sem rótulo `[Data: General Knowledge]` (ou equivalente) nem ressalva textual |

Se qualquer teste falhar, o problema mais provável está no
`community_report_graph.txt`/`community_report_text.txt` (persona/instruções
de citação) ou no modelo de `drift_search.completion_model_id` — não assuma
que é o índice em si sem checar essas duas coisas primeiro (ver `graphrag-
operations/references/edge-label-patch.md`).
