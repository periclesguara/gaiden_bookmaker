# Gaiden Bookmaker — perícia das migrations ausentes (Fase 0.5)

Data: 2026-07-29
Base examinada: `591973ffa616ce22b96b6537d98974bea160358d`
Escopo: recuperação forense e reconstrução fora da linha ativa. A Fase 1 não foi iniciada.

## Conclusão executiva

Nenhuma das três fontes originais foi recuperada. A busca cobriu histórico Git completo, branches locais e remotas, tags, reflogs, três stashes, seis worktrees, objetos inalcançáveis, histórico local do VS Code, histórico de shell filtrado, backups do repositório e dos bancos, preservação da Fase 0 e o catálogo PostgreSQL somente leitura.

O resultado é:

| Migration | Classe | Confiança | Resultado |
|---|---:|---:|---|
| `editorial.0017_editionpipeline_markitdown_stages` | C | 66% | `AlterField` é provável, mas labels, ordem e eventual conjunto de choices sem linhas persistidas não são verificáveis |
| `pipeline.0016_pipelinejob_markitdown_stages` | C | 72% | quatro valores são comprovados pelos dados; labels, ordem e choices não usados não são verificáveis |
| `pipeline.0017_bookeditiontemplate_historic_fields` | B | 93% | schema, ordem física, defaults, nulabilidade, dados e `ProjectState` esperado permitem reconstrução semanticamente determinística |

Não há candidato classe A nem migration comprovadamente redundante (classe D). Os três candidatos permanecem exclusivamente em `/tmp/gaiden_phase05_recovery_20260729`; nenhum foi colocado em `web/*/migrations` da linha ativa.

Apesar da classificação B da terceira migration, **nenhum candidato deve ser ativado**: sua dependência nominal é a `pipeline.0016` ainda classificada como C. Também não há base para baseline ou `--fake`.

## Invariantes preservados

- banco canônico `gaiden`: somente consultas com `default_transaction_read_only=on`;
- nenhum `migrate`, DDL, backfill ou alteração de dados no banco canônico;
- diretórios ativos de migrations: inalterados;
- nenhum baseline, commit, push, merge ou PR;
- candidatos e junção de grafo para teste: somente em diretórios temporários;
- nenhum arquivo editorial, build ou manifest de produção alterado;
- Fase 1 não iniciada.

## Cobertura forense

### Git e filesystem

- 39 branches locais, 34 refs remotas, 24 tags, 3 stashes e 6 worktrees examinados;
- `git log --all --full-history -- <path>`: zero commits para cada um dos três paths;
- `git rev-list --all --objects`: zero objetos com qualquer um dos três nomes;
- enumeração de todos os blobs Git, alcançáveis e inalcançáveis: nenhuma fonte ou conteúdo com os nomes das migrations/campos históricos;
- `git fsck --full --no-reflogs --unreachable`: 3.547 blobs, 33 commits, 920 trees e 1 tag inspecionados, sem candidato;
- busca por filename em `/home/periclesguara`: zero cópias dos três arquivos;
- reflogs e stashes não contêm os arquivos; o stash de não rastreados contém apenas `scripts/pipeline/run_markitdown_preprod.py` relacionado ao assunto;
- histórico local do VS Code, lixeira, histórico de shell filtrado e logs PostgreSQL disponíveis: nenhuma fonte;
- backups SQLite, dump lógico PostgreSQL de 2026-07-15, dump preservado de 2026-07-23 e diretórios `backups/`: apenas schema/dados/registro de aplicação, sem código da migration.

### Evidência da Fase 0

- manifest preservado: SHA-256 `883c48046423d66d98e663adea8bb8493b107751a4b6c88f18f0aa3ffb57698c`;
- matriz de migrations: SHA-256 `2a1af68f3d3986c4d2c9ce6ec27629b84519800af3b540d5b057c4da3824fce4`;
- a matriz confirma as três como aplicadas e sem fonte, entre 26 registros ausentes da linha ativa;
- os 23 outros arquivos recuperáveis preservados não contêm dependências posteriores que revelem operações ou hashes dessas três migrations.

### Registro canônico somente leitura

| Ordem | Migration | Aplicada em UTC |
|---:|---|---|
| 1 | `pipeline.0016_pipelinejob_markitdown_stages` | 2026-05-16 01:54:46.575490 |
| 2 | `editorial.0017_editionpipeline_markitdown_stages` | 2026-05-16 01:54:47.849446 |
| 3 | `pipeline.0017_bookeditiontemplate_historic_fields` | 2026-05-16 01:56:03.826885 |

Esses timestamps comprovam aplicação e ordem temporal, mas não revelam dependências cruzadas nem operações. A numeração contígua e os modelos afetados sustentam dependências locais; nenhuma relação de model introduzida por essas operações exige dependência entre `editorial` e `pipeline`.

## 1. `editorial.0017_editionpipeline_markitdown_stages`

### Evidência encontrada

- dependência anterior ativa mais provável: `editorial.0016_editionpipeline_build_outdated_and_more`;
- `EditionPipeline.current_stage` antes do alvo era `CharField(max_length=20, default="RAW", choices=PipelineStage.choices)`;
- o commit `b5097d2768cfde7d7cf5943af72bc9f99017e4bd`, de 2026-03-05, comprova o uso operacional de `HTML_UPLOADED`, `HTML_PREPROD_READY` e `MD_SOURCE_READY` antes da aplicação da migration;
- o banco canônico contém uma linha em `MD_SOURCE_READY`;
- a coluna canônica continua `varchar(20) NOT NULL`, sem default de banco e sem `CHECK` de choices;
- `SOURCE_EXTRACTED`, também observado hoje, é de fluxo posterior e não foi atribuído à migration de maio.

### Dependência e operações prováveis

Dependência provável: `editorial.0016_editionpipeline_build_outdated_and_more`.

Operação provável única: `AlterField(EditionPipeline.current_stage)`, preservando `max_length=20` e `default="RAW"`, com inclusão pelo menos dos três estados HTML/MarkItDown acima. O candidato temporário serializa essa hipótese; seus labels e a posição exata na lista não são prova histórica.

### Impactos

- schema: nenhum DDL material esperado; tipo e nulabilidade não mudaram;
- estado Django: altera choices serializados no `ProjectState`;
- dados: nenhum movimento ou conversão esperado;
- defaults: sem mudança provável;
- dependência cruzada: nenhuma evidência;
- `RunPython`, `RunSQL`, `SeparateDatabaseAndState`: improváveis e desnecessários para o efeito observado.

### Classificação e risco

- classe: **C — reconstrução parcial ou não verificável**;
- possibilidade de recuperação local restante: baixa; apenas backup externo/offline ou fonte de outro clone pode elevar a classe para A;
- risco de baseline: alto, pois um estado de choices inventado pode fazer migrations posteriores parecerem compatíveis sem reproduzir o `ProjectState` original;
- recomendação: não ativar; procurar a fonte externa ou obter definição explícita e verificável de todos os choices e labels.

## 2. `pipeline.0016_pipelinejob_markitdown_stages`

### Evidência encontrada

- dependência anterior ativa mais provável: `pipeline.0015_bookeditiontemplate_epilogue_text_and_more`;
- o estado anterior comprovado em `pipeline.0003` tem `raw`, `normalize`, `split`, `translate`, `refine` e `polish`;
- a coluna canônica permanece `varchar(50) NOT NULL`, sem default e sem `CHECK`;
- dados canônicos comprovam os valores adicionais:

| Valor | Linhas |
|---|---:|
| `raw_uploaded` | 1 |
| `markitdown_inspected` | 2 |
| `markitdown_extracted` | 2 |
| `md_ready` | 2 |

Os três valores mais específicos de MarkItDown não existem hoje em arquivos rastreados nem em blobs Git. Assim, os dados comprovam os valores, mas não seus labels, ordenação ou choices eventualmente nunca usados.

### Dependência e operações prováveis

Dependência provável: `pipeline.0015_bookeditiontemplate_epilogue_text_and_more`.

Operação provável única: `AlterField(PipelineJob.stage)` preservando `max_length=50` e acrescentando os quatro valores observados.

### Impactos

- schema: nenhum DDL material esperado;
- estado Django: altera choices serializados de `PipelineJob.stage`;
- dados: nenhum movimento esperado; as linhas existentes apenas passam a ser representáveis pelo state;
- defaults: nenhum;
- dependência cruzada: nenhuma evidência;
- `RunPython`, `RunSQL`, `SeparateDatabaseAndState`: improváveis e desnecessários.

### Classificação e risco

- classe: **C — reconstrução parcial ou não verificável**;
- possibilidade de recuperação local restante: baixa;
- risco de baseline: alto, agravado por ser a dependência nominal da `pipeline.0017`;
- recomendação: não ativar e não usar o candidato temporário como fonte; recuperar o arquivo original ou comprovar integralmente choices/labels.

## 3. `pipeline.0017_bookeditiontemplate_historic_fields`

### Evidência encontrada

As duas colunas são consecutivas no catálogo (`attnum` 48 e 49) e exatamente posteriores aos campos de prefácio/introdução/epílogo da `pipeline.0015`:

| Coluna | Tipo | Nula | Default de banco |
|---|---|---:|---|
| `original_publication_historic` | `varchar(32)` | não | `''` |
| `original_author_death_historic` | `varchar(32)` | não | `''` |

Há 159 linhas; nenhuma contém `NULL`, duas têm publicação histórica não vazia e uma tem falecimento histórico não vazio. Os valores `380 a.C.` e `375 a.C.` demonstram por que `CharField` coexistia com os `DateField` modernos: datas anteriores à era comum não cabem no contrato de data atual. O dump lógico de julho confirma o mesmo tipo, ordem, default e dados.

O default persistente no PostgreSQL é consistente com o padrão histórico do projeto em `pipeline.0015`: DDL condicional dentro de `RunPython` e state separado. Uma `AddField` Django convencional não explica tão bem a permanência do default no catálogo.

### Dependência e operações reconstruídas

Dependência provável: `pipeline.0016_pipelinejob_markitdown_stages`.

Reconstrução classe B:

1. `SeparateDatabaseAndState`;
2. `database_operations`: `RunPython` idempotente que cria, apenas se ausentes, as duas colunas `varchar(32) NOT NULL DEFAULT ''`; reverse `noop` para não destruir valores históricos;
3. `state_operations`: dois `AddField` com `CharField(max_length=32, blank=True, default="")`.

O candidato não usa `migrations.RunSQL`; o `RunPython` executa DDL SQL delimitado e idempotente. Não há alteração de choices nem dependência cruzada.

### Impactos

- schema: acrescenta exatamente as duas colunas observadas, na ordem canônica;
- estado Django: acrescenta os dois `CharField` opcionais com default vazio;
- dados: na criação inicial, linhas preexistentes recebem `""`; em schema já existente, valores não vazios são preservados;
- defaults: adiciona e preserva default de banco vazio;
- reversão: intencionalmente não destrutiva;
- movimentação: não converte nem copia para os `DateField`; preserva o texto histórico.

### Classificação e risco

- classe: **B — reconstrução semanticamente determinística**;
- confiança: 93%; a implementação original não foi recuperada, mas estado, schema e semântica de preservação são reproduzíveis;
- possibilidade de recuperação: o candidato é tecnicamente recuperável, porém não ativável enquanto a dependência `pipeline.0016` for C;
- risco de baseline: médio para a operação isolada, alto para a integração do grafo incompleto;
- recomendação: preservar o candidato e suas evidências; não copiar para a linha ativa.

## Detecção de operações especiais

| Migration | `RunPython` | `RunSQL` | `SeparateDatabaseAndState` | Choices | Defaults | Dados | Cruzada |
|---|---:|---:|---:|---:|---:|---:|---:|
| `editorial.0017...markitdown_stages` | não provável | não provável | não provável | sim | não | não | não evidenciada |
| `pipeline.0016...markitdown_stages` | não provável | não provável | não provável | sim | não | não | não evidenciada |
| `pipeline.0017...historic_fields` | sim na reconstrução B | não como operação; SQL interno ao `RunPython` | sim | não | sim | preenchimento vazio e preservação | não evidenciada |

Essa tabela distingue “operação original provável” de “mecanismo escolhido para a reconstrução B”. Sem a fonte, não é possível afirmar se o original usava exatamente `RunPython`, `RunSQL`, `SeparateDatabaseAndState` ou se foi marcado `--fake` após DDL de runtime; é possível afirmar o efeito semântico exigido pelo catálogo e pelos dados.

## Validação isolada do candidato B

O teste foi executado numa cópia temporária do commit-base com:

- o candidato B;
- o candidato C de `pipeline.0016` apenas como predecessor nominal;
- um merge **exclusivamente de teste**, `pipeline.0018_phase05_test_merge`, sem operações, para unir o ramo histórico ao `pipeline.0016_official_body_models` atual;
- models temporários representando o `ProjectState` candidato.

Nada desse grafo foi copiado para a linha ativa.

### PostgreSQL vazio

- banco isolado com extensão `vector` já provisionada;
- `manage.py check`: passou;
- `makemigrations --check --dry-run`: nenhuma mudança antes ou depois de migrar;
- `migrate --plan`: mostrou o ramo reconstruído;
- migração integral: passou, incluindo `pipeline.0016`, `pipeline.0017` e a junção de teste;
- segundo `migrate --plan`: nenhuma operação;
- schema final versus models temporários: zero tabelas/colunas ausentes ou extras;
- `ProjectState`: ambos os campos como `CharField(max_length=32, blank=True, default="")`.

### Clone isolado do canônico

- os três registros históricos já existiam, como esperado;
- plano inicial: somente a junção vazia de teste;
- plano final: nenhuma operação;
- nenhuma coluna faltante ou extra em `BookEditionTemplate` e `PipelineJob` perante os models candidatos;
- 159 linhas preservadas; contagens não vazias continuaram 2 e 1;
- a função condicional do candidato B foi chamada duas vezes após aplicação, sem erro e sem duplicar colunas.

### Testes e segurança

- 4/4 testes relacionados passaram: segurança de schema e round-trip/default dos campos históricos;
- queryset comum de `BookEditionTemplate`: quatro consultas de introspecção/leitura e zero `ALTER`, `CREATE`, `DROP` ou `TRUNCATE`;
- schema vazio: [evidência JSON](/tmp/gaiden_phase05_recovery_20260729/schema_empty_candidate.json), SHA-256 `4663f75e8b2431062d97a39810718029260d123e5b60d46c7d8cb834aab26c8f`;
- schema clone: [evidência JSON](/tmp/gaiden_phase05_recovery_20260729/schema_clone_candidate.json), SHA-256 `ad7232ee5addb99591a57e508a93939d118598b0362924ce092a54e9b75f8b7a`.

Após a coleta, `gaiden_phase05_empty_20260729_b1`, `gaiden_phase05_clone_20260729_b1` e `test_gaiden_phase05_empty_20260729_b1` foram removidos. O harness de teste foi movido para a lixeira recuperável; apenas o diretório de recuperação e as evidências hashadas foram mantidos em `/tmp`.

## Candidatos preservados

| Migration | Path temporário | SHA-256 |
|---|---|---|
| `editorial.0017_editionpipeline_markitdown_stages` | `/tmp/gaiden_phase05_recovery_20260729/web/editorial/migrations/0017_editionpipeline_markitdown_stages.py` | `1ecd181df618e7cb9e5defe3af24b18c65555de1b94fb19eb9b98360f6d85aec` |
| `pipeline.0016_pipelinejob_markitdown_stages` | `/tmp/gaiden_phase05_recovery_20260729/web/pipeline/migrations/0016_pipelinejob_markitdown_stages.py` | `0c37594ae409c000aedd0d3118c85b2f94a2475539be26f3d0bf246f8e02bf84` |
| `pipeline.0017_bookeditiontemplate_historic_fields` | `/tmp/gaiden_phase05_recovery_20260729/web/pipeline/migrations/0017_bookeditiontemplate_historic_fields.py` | `a98c611fe0d5b6d6140fe1b93930917d523def2a862b3a41f96d49c449077ab5` |

A descrição estruturada das operações está em `docs/audits/phase05_candidate_operations.json`.

## Recomendação final e gate

1. Não ativar nenhuma das três reconstruções.
2. Não criar baseline e não usar `--fake`.
3. Buscar os dois arquivos classe C em backups externos, snapshots de VM, outro clone ou mídia não montada nesta máquina.
4. Se a fonte não existir, exigir aprovação explícita de uma especificação completa dos choices e labels antes de elevar C para B.
5. Somente depois reconciliar branches históricas e criar migrations novas de merge; isso pertence a uma autorização posterior.

Status ao encerrar: **Fase 0.5 concluída; Fase 1 permanece bloqueada**.
