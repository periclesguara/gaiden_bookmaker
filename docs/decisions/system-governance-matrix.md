# System Governance Matrix

Status: accepted

Date: 2026-04-08

## Princípio mestre

- código novo nasce em áreas `[oficial]`
- `[compat]` sobrevive para sustentar transição, não para liderar
- `[legado]` é contido, não expandido
- `[passivo]` não vira centro de verdade por conveniência

## Matriz operacional

### Raiz do projeto

| Área | Classe | Papel | Permitido | Proibido |
| --- | --- | --- | --- | --- |
| `/data` | `[oficial]` | storage canônico | artefatos oficiais, outputs do pipeline, artifacts da Collection | storage primário paralelo, bypass por `web/data` |
| `/docs` | `[oficial]` | governança, diagnósticos, runbooks | ADRs, diagnostics, runbooks, snapshots | regra estrutural importante sem documentação |
| `/gaiden` | `[oficial]` | núcleo Python | lógica central, orquestração, infraestrutura, interfaces | lógica nova em wrappers históricos |
| `/web` | `[oficial]` | camada Django | views leves, forms, services web, integração com application/infrastructure | lógica estrutural pesada em views, merge em views |
| `docker/` | `[oficial]` | containerização | Dockerfiles e runtime oficial | container paralelo sem contrato |
| `docker-compose.core.yml` | `[oficial]` | orquestração principal | serviços oficiais | runtime informal fora da governança |
| `pyproject.toml` | `[oficial]` | contrato de build/configuração | packaging e dependências oficiais | desvio informal de runtime |
| `/assets` | `[oficial]` | apoio visual | logos e assets estáveis | uso como storage operacional |

### Storage canônico

| Área | Classe | Papel | Permitido | Proibido |
| --- | --- | --- | --- | --- |
| `/data/builds` | `[oficial]` | outputs finais | artefatos finais de build | uso como source primário |
| `/data/chunks` | `[oficial]` | chunks canônicos | chunks e manifests de chunk | dumping ground sem contrato |
| `/data/collections` | `[oficial]` | storage da Collection | uploads, prepared, normalized_items, merged, audit, manifest | uploads de Collection em `data/raw/book_XXXX` ou `web/data` |
| `/data/covers` | `[oficial]` | covers oficiais | artefatos de capa | cache arbitrário |
| `/data/db` | `[oficial]` | DB em storage canônico | persistência oficial conforme política | múltiplos DBs concorrentes sem governança |
| `/data/editions` | `[oficial]` | artefatos editoriais | outputs de edição oficiais | dumping informal |
| `/data/extracted_prefaces` | `[oficial]` | outputs de prefácio | outputs controlados da etapa | uso difuso sem contrato |
| `/data/frontmatter` | `[oficial]` | frontmatter canônico | frontmatter oficial | duplicidade principal em `web/data/frontmatter` |
| `/data/images` | `[oficial]` | imagens editoriais | imagens e artefatos correlatos | misturar com lixo/caches arbitrários |
| `/data/md` | `[oficial]` | MD canônico | MD oficial do pipeline | MD principal paralelo |
| `/data/normalized` | `[oficial]` | normalized canônico | normalized oficial | normalized paralelo |
| `/data/preprod` | `[oficial]` | outputs preprod | HTML preprod e afins | storage genérico |
| `/data/raw` | `[oficial]` | input cru de Book | raw oficial de Book | uploads primários da Collection |
| `/data/tmp` | `[oficial]` | temporários oficiais | transitórios controlados | virar storage estável |
| `/data/translated` | `[oficial]` | traduzidos canônicos | outputs de tradução | duplicidade principal em `web/data/translated` |
| `/data/collections/collection_0001` | `[oficial]` | collection real | artifacts oficiais da collection | uso como pasta genérica de teste |
| `/data/collections/collection_test_*` | `[passivo]` | artefato de teste | fixture e teste controlado | uso produtivo sem promoção explícita |

### Documentação e governança

| Área | Classe | Papel | Permitido | Proibido |
| --- | --- | --- | --- | --- |
| `/docs/decisions` | `[oficial]` | ADRs | registrar centros de verdade e políticas | regra estrutural sem ADR |
| `/docs/diagnostics` | `[oficial]` | auditoria | inventário, hardcodes, resíduos, audits | diagnóstico divergente da realidade |
| `/docs/runbooks` | `[oficial]` | operação | runbooks oficiais | fluxo oficial sem runbook |

### Núcleo Python oficial

| Área | Classe | Papel | Permitido | Proibido |
| --- | --- | --- | --- | --- |
| `/gaiden/application` | `[oficial]` | orquestração | casos de uso, sequência operacional | duplicar infraestrutura |
| `/gaiden/application/collections` | `[oficial]` | orquestração da Collection | validar e coordenar prepare/normalize/merge/handoff | deixar lógica real na web |
| `/gaiden/application/pipeline` | `[oficial]` | orquestração do pipeline | gates, status, ingest/normalization/translation oficiais | competir com scripts como centro real |
| `/gaiden/domain` | `[oficial]` | domínio | conceitos e regras puras | acoplamento desnecessário com web/IO |
| `/gaiden/infrastructure` | `[oficial]` | IO e integrações | storage, env, OpenAI, runners | lógica de negócio web |
| `gaiden/infrastructure/storage.py` | `[oficial]` | resolver geral | resolução de path oficial | concorrente não governado |
| `gaiden/infrastructure/collections_storage.py` | `[oficial]` | resolver da Collection | uploads/prepared/normalized_items/merged/audit/manifest | bypass por hardcode |
| `gaiden/infrastructure/collections_runner.py` | `[oficial]` | executor da Collection | prepare, normalize, merge, artifacts | depender de view/template |
| `gaiden/infrastructure/env.py` | `[oficial]` | centro de env/config | resolver e validar ambiente | loaders paralelos espalhados |
| `gaiden/infrastructure/openai_client.py` | `[oficial]` | cliente OpenAI oficial | chamadas externas oficiais | competir com wrapper da raiz |
| `/gaiden/interfaces` | `[oficial]` | interfaces externas | CLIs e bridges oficiais | duplicar application |
| `gaiden/interfaces/cli.py` | `[oficial]` | CLI geral | entrada operacional oficial | competir com scripts sem motivo |
| `gaiden/interfaces/collections_cli.py` | `[oficial]` | CLI da Collection | criar collection, itens, prepare, normalize, merge, handoff | lógica paralela à web/application |

### Módulos compatíveis da raiz

| Área | Classe | Papel | Permitido | Proibido |
| --- | --- | --- | --- | --- |
| `gaiden/about.py` | `[compat]` | wrapper histórico | ponte compatível | lógica nova principal |
| `gaiden/ingest.py` | `[compat]` | wrapper histórico | compatibilidade transitória | novo centro de ingestão |
| `gaiden/normalize.py` | `[compat]` | wrapper histórico | compatibilidade transitória | lógica nova estrutural |
| `gaiden/openai_client.py` | `[compat]` | wrapper do cliente antigo | redirecionar ao cliente oficial | ser o cliente principal |
| `gaiden/secrets.py` | `[compat]` | shim histórico | compatibilidade | centro oficial de env/secrets |
| `gaiden/translate.py` | `[compat]` | wrapper histórico | compatibilidade | centro real de tradução |
| `gaiden/tools` | `[compat]` | utilitários controlados | ferramentas auxiliares | virar camada central sem reclassificação |
| vários módulos históricos da raiz | `[legado/passivo]` | histórico técnico | referência controlada | dependência nova |

### Legado

| Área | Classe | Papel | Permitido | Proibido |
| --- | --- | --- | --- | --- |
| `/legacy` | `[legado]` | histórico explícito | consulta e compatibilidade extrema documentada | imports novos |
| `/legacy/gaiden` | `[legado]` | código histórico isolado | sobrevivência controlada | qualquer centro de verdade |

### Scripts e entrypoints compat

| Área | Classe | Papel | Permitido | Proibido |
| --- | --- | --- | --- | --- |
| `/scripts` | `[compat/passivo]` | scripts históricos, wrappers, one-offs | compatibilidade e operações pontuais | substituir CLI oficial como padrão |
| `scripts/build_sherlock_md_final*.py` | `[compat]` | build histórico compatível | uso controlado | expansão como padrão oficial |
| `scripts/migrate_sqlite_books_to_postgres.py` | `[compat]` | migração compatível | uso controlado | rotina cotidiana sem governança |
| `scripts/normalize_sherlock_adventures_en.py` | `[passivo]` | one-off | referência | caminho oficial |
| `scripts/open_gaiden` | `[compat]` | wrapper operacional | compatibilidade | competir com CLI oficial |
| `scripts/polish_merge_refine_clean.py` | `[passivo]` | one-off histórico | referência | renascer como fluxo oficial |
| `run_gaiden.sh` | `[compat]` | wrapper histórico | compatibilidade | esconder lógica nova principal |
| `dev.sh` | `[compat]` | entrada auxiliar de dev | facilitar desenvolvimento | contrato operacional principal |
| `env.sh` | `[compat]` | bootstrap auxiliar | compatibilidade | substituir `env.py` como centro de verdade |

### Camada web oficial

| Área | Classe | Papel | Permitido | Proibido |
| --- | --- | --- | --- | --- |
| `/web/collections_module` | `[oficial]` | app web da Collection | cadastro, upload, views leves, services web, templates | merge em views, storage fora de `data/collections` |
| `/web/editorial` | `[oficial]` | app editorial | frontmatter, KDP e serviços oficiais | competir com Collection no que é específico da Collection |
| `/web/gaiden_portal` | `[oficial]` | portal principal | roteamento, configuração, tela inicial | lógica pesada de pipeline |
| `/web/pipeline` | `[oficial]` | app web do pipeline | UI do pipeline, services web, testes arquiteturais | virar dono da Collection |
| `web/pipeline/test_architecture_contracts.py` | `[oficial]` | sentinela arquitetural | bloquear regressões | ser decorativo |
| `/web/data` | `[legado/passivo]` | passivo histórico | leitura residual documentada | storage primário novo |
| `/web/data/builds` | `[passivo]` | resíduo/compat | uso residual controlado | build oficial novo |
| `/web/data/db` | `[passivo]` | resíduo/compat | compatibilidade controlada | DB primário novo |
| `/web/data/frontmatter` | `[passivo]` | resíduo/compat | leitura residual | frontmatter oficial novo |
| `/web/data/translated` | `[passivo]` | resíduo/compat | leitura residual | translated oficial novo |
| `/web/Holmes`, `/web/When`, `/web/re` | `[passivo]` | resíduos auditados | retenção temporária controlada | uso novo ou dependência de código |
| `/web/db.sqlite3` | `[passivo]` | DB local auxiliar | dev/teste controlado | competir com DB oficial |

### Outros componentes

| Área | Classe | Papel | Permitido | Proibido |
| --- | --- | --- | --- | --- |
| `/backups` | `[passivo-controlado]` | retenção de backup | backup controlado | virar storage de trabalho |
| `/exports` | `[compat/passivo]` | exportações auxiliares | export controlado | centro de verdade |
| `/Downloads` | `[externo/passivo]` | material externo | referência manual | depender disso como parte oficial |
| `/sqlite3` | `[passivo]` | utilitário auxiliar | apoio técnico controlado | ser tratado como domínio do projeto |
| `/gaiden_bookmaker.egg-info` | `[passivo]` | resíduo de build | existir | qualquer papel operacional |

## Políticas globais

- código novo nasce somente em `[oficial]`
- `[compat]` só recebe wrapper, ponte ou manutenção mínima
- `[legado]` não recebe dependência nova
- `[passivo]` não vira oficial sem promoção explícita e documentada
- a Collection permanece blindada até `COLLECTION_MERGED`
- `web/data` não pode voltar a ser storage primário
- toda responsabilidade estrutural deve ter centro de verdade único
- toda exceção relevante deve estar documentada em `docs/decisions` ou `docs/diagnostics`

## Proibições executáveis

- criar novo storage oficial fora de `/data`
- usar `web/data` para fluxo oficial novo
- fazer merge da Collection em views
- criar lógica nova em wrappers compat
- importar legado em código novo
- deixar passivo virar centro de verdade por conveniência
- criar script novo sem classificação
- espalhar hardcoded paths
- tratar Collection como Book na entrada
