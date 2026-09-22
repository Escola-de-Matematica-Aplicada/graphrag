# Exemplo: sequência de iterações com `--method drift`

Demonstração de como usar o `drift` para uma sequência de perguntas
encadeadas (estilo "aula socrática"), sobre o tema da hierarquia
computacional em HPC. Complementa o artifact
[Hierarquia Computacional em HPC](https://claude.ai/code/artifact/668e0cc8-be7d-45d7-b5b8-6e3e79132a02).

## Por que `drift` para isso

Do seu próprio `GRAPH-RAG-CONTEXT.md`: *"O drift tende a ser o melhor dos
quatro para uso didático: parte de uma entidade e navega o grafo, então
responde perguntas de acompanhamento sem perder a ancoragem."*

**Detalhe importante**: isso vale *dentro* de uma única chamada — o `drift`
já faz múltiplos saltos internos no grafo (primer + follow-ups, por isso o
`drift_k_followups` e `n_depth` no `settings.yaml`). A CLI do `graphrag
query`, porém, **não mantém estado entre chamadas** (`graphrag query --help`
não tem flag de histórico/sessão). Uma "sequência de iterações" via CLI
significa: rodar uma consulta, ler a resposta, e escrever a próxima pergunta
citando explicitamente as entidades/conceitos que a resposta anterior
revelou — o encadeamento é feito por você, não pela ferramenta.

## A sequência (4 iterações, mesmo tema, afunilando)

### 1 — ponto de partida

```bash
graphrag query --root /workspaces/graphrag --method drift \
  "Na hierarquia computacional do HPC, o que é um domínio NUMA e por que ele \
  importa para o desempenho de um job paralelo?"
```

Revelou conceitos novos não pedidos explicitamente: `CHUNK` (unidade de
alocação do PBS), `PLACE_STATEMENT`, `aprun -S`, `PBScrayseg`,
`lplace=scatter`.

### 2 — puxando o fio de um conceito novo

```bash
graphrag query --root /workspaces/graphrag --method drift \
  "Como o conceito de CHUNK se relaciona com o PLACE_STATEMENT no PBS para \
  controlar a alocação NUMA de processos MPI dentro de um nó?"
```

Aprofundou em estratégias reais (`vscatter`, `pack`, `scatter`, `place=group`),
com exemplos de comando (`qsub -lselect=8:ncpus=1:PBScrayseg=1`) e a fórmula
`nchunk = n/S` para PEs por nó NUMA.

### 3 — testando o limite do grafo (conectar a um sistema real)

```bash
graphrag query --root /workspaces/graphrag --method drift \
  "Essas estratégias de placement NUMA no PBS (vscatter, PBScrayseg, \
  place=group) se aplicam a algum supercomputador Cray ou ao Santos Dumont \
  citado no material das aulas?"
```

Resultado **honesto, não inventado**: confirmou que as diretivas são do
ecossistema Cray-PBS, mas disse explicitamente que **não há evidência no
material indexado** de que o Santos Dumont use PBS Professional ou essas
estratégias — e sugeriu consultar a documentação oficial do LNCC em vez de
inferir. Isso é o comportamento correto de um sistema aterrado em fontes:
melhor admitir lacuna do que interpolar sistemas parecidos.

### 4 — fechando o ciclo, voltando à pedagogia

```bash
graphrag query --root /workspaces/graphrag --method drift \
  "Como os conceitos de NUMA, afinidade de processos e hierarquia de memória \
  são ensinados nas disciplinas de computação paralela (CPC869/COC762), em \
  termos de exercícios práticos ou benchmarks usados em aula?"
```

Fechou o ciclo com outra resposta honesta sobre os limites do corpus: achou
material didático real e concreto — hierarquia de memória na CPC-869 a
partir do modelo de Von Neumann, NAS Parallel Benchmarks (módulo BT, classes
A/B/C, 1-16 cores) rodados no cluster VENUS do NACAD e depois na Azure via
FGV, e um exercício de **thread-to-processor pinning no SGI Origin 3000**
comparando execução com e sem pinning — mas declarou explicitamente que
**NUMA e afinidade de processos não aparecem nomeados** nos materiais
indexados da CPC-869/COC762, e que a COC762 foca em Big Data (MapReduce/
Hadoop), não em NUMA. Sugeriu consultar as ementas oficiais para fechar a
lacuna, em vez de inventar uma conexão que o grafo não sustenta.

## Padrão geral para replicar

1. Pergunta inicial ampla o suficiente para o `drift` ancorar em uma
   comunidade/entidade central do tema.
2. Leia a resposta e escolha **um conceito específico que apareceu mas não
   foi pedido** — isso é sinal de que o grafo tem mais profundidade ali.
3. Pergunta de acompanhamento citando esse conceito pelo nome exato usado na
   resposta anterior (facilita a busca vetorial encontrar o mesmo nó).
4. Repita até ou (a) esgotar profundidade nova, ou (b) o sistema admitir que
   não tem a informação — ambos os desfechos são úteis: o segundo mostra os
   limites reais do corpus indexado, não uma alucinação.
