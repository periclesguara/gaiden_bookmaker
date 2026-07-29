# Gaiden Bookmaker — relatório da Fase 0

Data: 2026-07-29
Escopo: estabilização da base; nenhuma funcionalidade das Fases 1–8 foi iniciada.

## Conclusão executiva

A base limpa foi criada em `/home/periclesguara/Projetos/gaiden_bookmaker_phase0_integration`, na branch local `agent/pipeline-editorial-integration-v1`, a partir de `591973ffa616ce22b96b6537d98974bea160358d`.

O grafo ativo desse commit é reproduzível em PostgreSQL vazio quando a extensão de plataforma `vector` já está provisionada. Um clone isolado do banco canônico reconhece o grafo ativo sem migrations pendentes e sem perda de tabelas ou colunas. O código que fazia `ALTER TABLE` durante consultas de `BookEditionTemplate` foi substituído por uma verificação de schema somente leitura, coberta por teste unitário e teste PostgreSQL.

A Fase 1 **não está liberável ainda**. O banco canônico registra 26 migrations ausentes da linha ativa; 23 têm fonte verificável preservada, mas três continuam sem arquivo ou objeto Git recuperável. Ativar apenas a parte recuperada criaria um grafo histórico incompleto e potencialmente falso.

## Preservação

Raiz dos artefatos: `/home/periclesguara/Projetos/gaiden_phase0_preservation`

| Origem | Branch | Commit | Status | Preservado |
|---|---|---|---:|---|
| `/home/periclesguara/Projetos/gaiden_bookmaker` | `agent/gaiden-author-studio` | `85d04af935b313722d4f8c123e633efd7741eec2` | 197 entradas: 193 rastreadas e 4 não rastreadas | patch binário, manifest de 193 arquivos, cópia dos 4 não rastreados, 1.185 hashes de binários relevantes |
| `/home/periclesguara/Projetos/gaiden_bookmaker_pr6_validation` | `validate/block00-after-pr6` | `591973ffa616ce22b96b6537d98974bea160358d` | 11 entradas rastreadas | patch binário, manifest de 11 arquivos, 33 hashes de binários relevantes |

O manifest principal é `gaiden_phase0_preservation/manifest.json`. Os patches `tracked.patch` passam em `git apply --check`; os patches staged estão vazios, como esperado. As cópias dos quatro arquivos não rastreados e os arquivos binários inventariados foram revalidados por SHA-256. Os hashes do `git status --porcelain=v2 --branch -z` antes e depois são iguais em cada worktree.

Arquivos ignorados de dados e backups foram inventariados e hashados quando binários, mas não duplicados: os worktrees originais permanecem somente leitura. `.gaiden_secrets`, `.venv` e caches foram explicitamente excluídos. Nenhuma credencial foi registrada.

## Base de integração

- Path: `/home/periclesguara/Projetos/gaiden_bookmaker_phase0_integration`
- Branch: `agent/pipeline-editorial-integration-v1`
- Base: `591973ffa616ce22b96b6537d98974bea160358d`
- Ref equivalente: `origin/agent/block00-drive-official-v2`
- Snapshot histórico consultado: `02368738f87cb859abba47646e507e4ba985185e`
- Estado inicial: limpo
- Commits novos: nenhum
- Push, merge ou PR: nenhum

O commit-base já contém a linha integrada de Intake, Drive, manifests, `TranslationJob`, `OfficialBodySnapshot`, promoção atômica do corpo oficial, locks, hashes e supersessão. Nenhum arquivo inteiro foi copiado cegamente dos worktrees sujos.

As 11 alterações posteriores do worktree Drive ficaram fora porque misturam correções de storage/frontmatter com CSS final e tratamento editorial específico de `book_0033`. As alterações posteriores do worktree principal também ficaram fora porque misturam tradução/refine, interface, EPUB e `last_version`, itens alheios à estabilização. Todas continuam preservadas nos patches e manifests.

## Migrations

A matriz completa, com 64 registros dos apps `editorial`, `intake_module` e `pipeline`, está em:

- `docs/audits/phase0_migration_matrix.md`;
- `docs/audits/phase0_migration_matrix.json`.

Resumo:

| Classificação | Quantidade | Resultado |
|---|---:|---|
| `PRESENT_AND_MATCHING` | 38 | arquivo ativo e migration registrada |
| `APPLIED_FILE_MISSING`, recuperável em objeto Git local | 22 | fonte exportada para a preservação, não ativada |
| `APPLIED_FILE_MISSING`, recuperável do não rastreado preservado | 1 | `editorial.0017_editionpipeline_last_version`, não ativada |
| `APPLIED_FILE_MISSING`, sem fonte | 3 | bloqueio explícito |
| `FILE_PRESENT_NOT_APPLIED` | 0 | — |
| `PRESENT_BUT_DIVERGENT` | 0 no grafo ativo | divergência física é registrada separadamente no schema |

As três fontes ainda ausentes são:

1. `editorial.0017_editionpipeline_markitdown_stages`;
2. `pipeline.0016_pipelinejob_markitdown_stages`;
3. `pipeline.0017_bookeditiontemplate_historic_fields`.

Os 23 arquivos recuperáveis foram exportados somente como evidência para `gaiden_phase0_preservation/recovered_migrations/`. Nenhum foi colocado no diretório ativo de migrations, porque isso introduziria branches paralelas sem as três dependências históricas completas.

### Estratégia escolhida

Para banco vazio, usar o grafo ativo de `591973ff`. A extensão `vector` deve ser provisionada pelo administrador antes de `migrate`; depois disso, todas as migrations são aplicadas do zero e o schema coincide integralmente com os models ativos.

Para banco existente, preservar o schema histórico como superset. No clone, o Django reportou zero operações pendentes; nenhuma tabela ou coluna foi removida e nenhuma migration foi marcada com `--fake`. O código ativo não possui tabela nem coluna esperada ausente no clone.

Antes da Fase 1, é necessário escolher e validar um dos caminhos:

1. recuperar exatamente as três migrations restantes, ativar as branches históricas completas e criar apenas merges de grafo novos e verificáveis; ou
2. aprovar formalmente um baseline controlado para bancos existentes, mantendo separado o bootstrap de banco vazio e documentando o schema legado preservado.

Foram rejeitados nesta fase: arquivos vazios para satisfazer nomes, reconstrução por inferência, `--fake`, renumeração e ativação parcial das 23 migrations recuperadas.

## Comparação de schema

Os relatórios completos estão em `phase0_schema_canonical.json`, `phase0_schema_empty.json` e `phase0_schema_clone.json` neste diretório.

- Banco vazio migrado: nenhuma tabela/model ausente, nenhuma coluna ausente, nenhuma coluna extra e nenhuma tabela extra de aplicação.
- Banco canônico somente leitura: nenhuma tabela/model ou coluna ativa ausente; cinco models possuem colunas históricas extras e existem quatro tabelas históricas extras.
- Clone isolado: idêntico ao canônico na comparação normalizada, antes e depois de `migrate` sem operações.

Colunas históricas extras aparecem em `editorial.Work`, `editorial.Edition`, `editorial.EditionPipeline`, `editorial.PipelineArtifact` e `pipeline.BookEditionTemplate`. As tabelas históricas extras são `editorial_editionblock`, `pipeline_pipelinerun`, `pipeline_pipelinerunitem` e `pipeline_pipelinerunstate`.

## Código alterado

| Arquivo | Origem/finalidade |
|---|---|
| `web/pipeline/models.py` | mudança mínima da Fase 0: remove DDL de runtime e transforma a rotina em verificação somente leitura |
| `web/pipeline/test_schema_safety.py` | prova unitária para schema incompleto e prova PostgreSQL de que queryset comum não executa DDL |
| `scripts/phase0_audit_migrations.py` | auditoria somente leitura de arquivos versus `django_migrations` |
| `scripts/phase0_compare_schema.py` | comparação somente leitura entre models e schema PostgreSQL |
| `docs/audits/phase0_migration_matrix.*` | matriz completa e evidência de proveniência |
| `docs/audits/phase0_schema_*.json` | comparações de banco vazio, canônico e clone |
| `docs/audits/phase0_stabilization_report.md` | este relatório |

Não foram criadas migrations nem alterados models, rotas, telas, conteúdo editorial, frontmatter, CSS, EPUBs ou dados.

## Validação

Resultados principais:

- `git diff --check`: passou.
- `python manage.py check`: passou no PostgreSQL vazio e no clone.
- `python manage.py makemigrations --check --dry-run`: nenhuma mudança detectada.
- `python manage.py showmigrations --plan`: 64 migrations ativas aplicadas no banco vazio.
- PostgreSQL vazio: migrate integral passou após provisionamento prévio da extensão `vector`.
- Clone isolado: `migrate --plan` sem operações; `migrate` sem operações; schema normalizado idêntico ao canônico.
- Testes de ausência de DDL: 3/3 passaram.
- Suíte completa de Intake/Drive/manifests/`TranslationJob`/`OfficialBodySnapshot`: 136/136 passaram com clientes Drive simulados e storage temporário.
- Grupo de regressão de pipeline/arquitetura: 75 executados; 71 passaram e 4 falharam.

As quatro falhas restantes foram reproduzidas sem as alterações da Fase 0 diretamente no commit-base `591973ff`: três testes de upload procuram o arquivo em um path diferente do storage efetivo, e o sentinel de arquitetura encontra `web/data` em `gaiden/infrastructure/paths.py`. Portanto são falhas preexistentes, não regressões da integração.

O primeiro ensaio de banco vazio falhou somente porque `gaiden` não é superuser e não pode executar `CREATE EXTENSION vector`. O ensaio definitivo usou um banco sem tabelas de aplicação, mas com a extensão de plataforma já instalada, e passou integralmente.

## Segurança e invariantes

- O banco canônico foi acessado com transações `READ ONLY`; não recebeu migrations, DDL, backfill ou alteração de dados.
- Os bancos temporários tinham prefixos `gaiden_phase0_*` ou `test_gaiden_phase0_*` e foram validados nominalmente antes de qualquer escrita.
- `gaiden_phase0_empty_20260729_a1`, `gaiden_phase0_empty_20260729_a2`, `gaiden_phase0_clone_20260729_a1` e `test_gaiden_phase0_empty_20260729_a2` foram criados por esta tarefa e removidos após a coleta das evidências.
- Nenhum dump com dados foi colocado no Git.
- `book_0031` permaneceu intocado.
- Nenhum original, build, imagem ou manifest editorial foi movido, renomeado ou sobrescrito.
- Nenhum backfill foi executado no banco canônico.

## Gate para a Fase 1

Status: **bloqueada**.

Pré-condições restantes:

1. obter as três migrations sem fonte ou aprovar explicitamente a estratégia de baseline controlado;
2. decidir quais colunas/tabelas históricas serão novamente representadas por models e quais permanecerão legado somente preservado;
3. corrigir ou aceitar formalmente as quatro falhas preexistentes antes de usá-las como gate de regressão;
4. documentar o provisionamento administrativo da extensão `vector` para bancos novos.

Arquivos previstos para a Fase 1, ainda não alterados: `web/editorial/models.py`, `web/pipeline/models.py`, migrations novas após a decisão de grafo, `gaiden/domain/pipeline/states.py`, `gaiden/domain/pipeline/transitions.py`, `web/pipeline/services/executions.py` e testes correspondentes. A Fase 1 não foi iniciada.
