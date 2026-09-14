# Writer deployment and file-treatment workflow

## Geração atual: OpenAI Luna

O Writer usa exclusivamente `gpt-5.6-luna` em `https://api.openai.com/v1`
(Responses API), reutilizando `OPENAI_API_KEY` do ambiente ou do carregador de
segredos já existente. A chave nunca deve ser inserida em código ou documentação.
O modelo e o endpoint são próprios do Writer: `GAIDEN_QWEN_*`,
`GAIDEN_DEFAULT_MODEL` e `OPENAI_BASE_URL` não alteram essa rota.

Geração, merge, revisão e reparos que usam o gerador do Writer enviam o contexto
necessário à OpenAI. Os embeddings e índices RAG existentes continuam locais e
não precisam ser recriados. Não há carregamento de Qwen nem gate de GPU para
redação ou merge. Após o merge, Qwen3.5-4B mantém a memória narrativa estruturada
e Phi-4 Mini audita causalidade, temporalidade e coerência em endpoints locais
independentes. Outros módulos e seus modelos permanecem independentes.

Cada chamada usa `reasoning.effort=none`, `store=false`, timeout de 180 segundos,
limite de 256–32768 tokens e zero retries de transporte. Parâmetros de amostragem
locais (inclusive a temperatura solicitada pelo crítico) não são enviados.
Saídas vazias, incompletas ou erros interrompem o fluxo sem registrar o corpo da
resposta de erro. A aprovação humana e a preservação de sessões permanecem.

Reinicie o processo Django do Writer para carregar a mudança. A migração
`writer.0006_chapter_narrative_memory` é necessária; ela é apenas aditiva e não
reescreve sessões nem capítulos existentes. Não reinicie uma geração em
andamento. Testes automatizados usam mocks, sem chamadas pagas ou locais.

Referência: https://developers.openai.com/api/docs/models/gpt-5.6-luna


## Scope

This is the phase-2 operator workflow using OpenAI Luna generation and local RAG.
It adds Django records and controls for source selection, normalization,
vectorization, story bibles, chapter planning, generation sessions, and explicit
editorial finalization.

It does not store model weights, source manuscripts, normalized bodies, vector
indexes, or generated files in Git. Writer routes open directly without Django
authentication. Generation and finalization remain POST-only, and Django CSRF
validation remains active for form submissions.

Because Writer has no application-level login, deploy it only on loopback or a
trusted private network with access control at the host or reverse proxy. Never
expose Writer directly to the public internet.

## Deployment rule

Before deployment:

1. back up PostgreSQL and record the current application commit;
2. confirm that `GAIDEN_WRITER_SOURCE_ROOT` points to the read-only corpus
   directory and `GAIDEN_WRITER_STORAGE_ROOT` points to writable external
   storage;
3. verify ownership and permissions without making the source corpus writable
   to the web process;
4. verify the configured OpenAI credential and the local embedding, narrative
   memory, and logic-audit endpoints;
5. review `python web/manage.py migrate --plan`;
6. apply the Writer migration shown by `migrate --plan`; on this stacked branch,
   the verified PR #18 operations are preserved in the forward migration
   `writer.0003_writer_workflow` after the existing manuscript migrations;
7. run `python web/manage.py check` and the protected test suite;
8. confirm that `/writer/` opens directly in an anonymous browser session, but
   do not trigger paid or GPU work during deployment smoke tests;
9. verify that the host firewall or reverse proxy prevents untrusted access.

The migration creates new Writer tables only. It does not alter editorial or
pipeline tables and does not touch files.

Rollback:

1. stop new Writer actions;
2. preserve the database and external Writer directory;
3. redeploy the previous application commit;
4. leave the new tables in place until a reviewed forward decision is made;
5. derived indexes may be deleted and rebuilt, but canonical sources and
   finalized chapter records must not be deleted.

## Six-stage operator flow

The project page is the operational dashboard for this flow. It validates the
selected-source counts, the on-disk index header, configured embedding model,
local model availability, required creative fields, chapter parameters, session
counts, and chapter status. Green stages are complete, the amber stage is the
next operator action, and gray stages are waiting on earlier prerequisites.
Stage and action buttons remain clickable in every state. Processing actions
open an in-page confirmation dialog before POST. A first run offers Confirm or
Cancel; a completed normalization or vectorization offers Remake or Cancel and
explains the impact. Remaking normalization invalidates related derived indexes;
remaking vectorization atomically rebuilds the derived index. Direct POSTs that
omit the required confirmation are rejected without processing. Pending actions
report their exact prerequisites.
The model availability probe runs only for a loopback OpenAI-compatible endpoint
and uses a short timeout; non-loopback services are not contacted by a page view.

### 1. Select and normalize files

The Files page first discovers `READY` canonical texts from Author Studio and
registers them by work code and title without copying or rewriting their files.
These canonical texts are immediately selectable as normalized RAG sources.
When `GAIDEN_WRITER_SOURCE_ROOT` is configured, the same action also discovers
additional UTF-8 `.txt` and `.md` files below that directory; the operator
checks those additional rows and sends them to Normalize.

Normalization is deterministic and records source SHA-256, normalized SHA-256,
provider, applied rules, character counts, output path, timestamp, and errors.
It can remove:

- Project Gutenberg start/end contracts and licenses;
- Internet Archive wrapper material;
- Standard Ebooks metadata, license and colophon material;
- YAML frontmatter and HTML tags in text exports;
- identified publisher backmatter that is not part of the narrative.

A narrative `PROLOGUE`, `PREFACE`, `INTRODUCTION`, chapter, part, book, or
`EPILOGUE` is body text. In particular, EPILOGUE is never a generic removal
marker. Unknown sources are not cut at arbitrary headings. If cleaning leaves
fewer than 500 characters, the operation fails for manual review.

Normalized files are content-addressed and written atomically. The source is
never overwritten.

### 2. Select and vectorize

A project selects only normalized files. Changing that selection invalidates
the previous index path. The Vectorize button creates a complete new index for
the selected set through Qwen3-Embedding-0.6B. The verified local Ollama tag is
`qwen3-embedding:0.6b`; chapter writing uses OpenAI `gpt-5.6-luna`.

All selected files must be represented. The index is an external, derived,
atomic artifact and can be rebuilt from normalized sources.

### 3. Fill the creative bibles

Each project has explicit fields for:

- character bible;
- supporting-characters bible;
- antagonist bible;
- scenarios and locations;
- world, period, climate and references;
- premise;
- story direction;
- general story outline.

Generation is blocked while any required bible or direction field is empty.

### 4. Direct and script the story

Every chapter has its own title, direction, and script. Project bibles and the
general outline provide global continuity. Completed earlier sessions provide
local continuity to the next session.

Retrieved corpus text is untrusted reference data. It cannot change system
rules or promote output.

### 5. Chapter parameter table

The project declares its chapter count, default 10. Saving creates missing rows
such as Chapter 01 through Chapter 10. Existing chapter rows are never deleted
when editing the project.

Each chapter row contains:

| Parameter | Rule |
|---|---|
| Target words | default 2,500; range 400–12,000 |
| Estimated tokens | displayed as approximately words × 1.45 |
| Sessions | integer from 1 to 4; default 4 |
| Minimum session size | 400 words |
| RAG results | 1–20; default 8 |
| Status | planned, generating, merging, merged, failed, final |

For 2,500 words and four sessions, the working target is approximately 625
words per session. The model may vary naturally; actual word count is recorded.

### 6. Generate, merge, remember, audit, and finalize

The project has one POST-only, CSRF-protected **Generate** button. The dashboard
selects the earliest eligible chapter whose predecessors are already merged or
finalized and asks the operator to confirm that exact chapter. A stale
confirmation is rejected if the eligible chapter changes. There is no Generate
button beside each chapter row.

One authorized run generates only the missing configured sessions in order and
records immutable session rows containing content, actual word count, model,
retrieval IDs, scores, and parameters. It then uses the Writer model to merge
the ordered sessions into one continuous chapter, removing repetition and
repairing transitions without changing the macro plot.

After the merge, local Qwen3.5-4B receives the previous cumulative state plus
the merged chapter and returns a strict JSON narrative state: events, character
locations/status/knowledge, timeline, open and resolved threads, clues, objects,
relationship changes, remembered facts, and consequences. Local Phi-4 Mini then
returns a strict causal, temporal, and coherence audit. A `REVIEW_REQUIRED`
verdict remains visible for the editor's explicit decision; suggested
micro-bridges never rewrite the chapter automatically.

Completed stages are never overwritten. A resumed run skips existing sessions,
the merge, or the narrative state as applicable. The merge is bound to a SHA-256
of the ordered scene texts. `MERGED` is still a Writer draft, not canonical
editorial content. The editor separately reviews it and explicitly finalizes the
same preserved merge.

The additive migration stores the merge, input hash, model names, cumulative
state, audit and timestamps on `writer.Chapter`. Before applying it to a
persistent database, make a verified backup, rehearse on a disposable clone and
review `migrate --plan`. Rollback should redeploy the earlier code while leaving
the additive columns in place; reversing the migration would discard this new
operator data and therefore requires an export or backup restoration plan.

## Operational limitations

Generation currently runs synchronously in the web request. This is acceptable
for initial controlled operation but may exceed reverse-proxy timeouts. Before
multi-user or unattended production, move generation to a durable job queue
with idempotency keys, progress reporting, cancellation, and worker limits.

Do not expose local model endpoints publicly. Do not put complete manuscripts
or prompt contents in application logs.


## Cenas por sessão

Nos parâmetros do capítulo, cada uma das 1–4 sessões possui uma lista ordenada
com os botões “+ Adicionar cena” e “− Remover cena”. Cada sessão aceita de 1 a 30
cenas, com até 10000 caracteres por cena. Preencha todas as sessões para ativar
esse planejamento. A direção e o roteiro gerais passam a ser contexto opcional.
Ao reduzir a quantidade de sessões, apenas as sessões visíveis serão salvas;
os campos das demais permanecem em memória até sair da página.

A geração recebe somente as cenas da sessão atual, em ordem, e o texto anterior
para continuidade. As cenas usadas são registradas nos parâmetros da sessão.
Isso delimita os acontecimentos solicitados, mas não garante que o modelo nunca
repita texto; a revisão editorial continua necessária. As cenas de sessões já geradas ficam preservadas. É permitido preencher ou
modificar cenas das sessões ainda não geradas; os demais parâmetros do capítulo
continuam imutáveis após a primeira sessão.
Capítulos sem cenas conservam o fluxo anterior de direção e roteiro gerais.

A migração `writer.0005_chapter_session_scenes` acrescenta uma coluna JSON com
lista vazia por padrão, sem alterar manuscritos nem sessões geradas. Depende da
migração local `0004_storyproject_supporting_characters_bible`. Antes de aplicar
em banco persistente, verificar backup restaurável, ensaiar em clone descartável
e revisar `migrate --plan`. Para reverter a interface, restaurar o código anterior
e manter a coluna; reverter a migração apaga os planos de cenas e exige exportação
prévia ou recuperação do backup.


### Limite do roteiro por sessão

Com cenas configuradas, o pedido de geração exclui a direção e o roteiro gerais
do capítulo: somente o plano da sessão governa seus acontecimentos. As bíblias
e o roteiro da história continuam sendo contexto. O Writer rejeita uma repetição
exata de 40 palavras consecutivas de sessões anteriores antes de salvar a nova
sessão. A revisão de progressão e revelações continua necessária.

Na configuração histórica Qwen (não usada pela geração atual), a variante `gaiden-writer-qwen3.5:9b-scenes-v2` reutiliza os
pesos instalados, com contexto de 16384 tokens e um template de conversa textual
com papéis system/user/assistant e marcadores Qwen. A escolha é feita pela variável
`GAIDEN_QWEN_MODEL`. Modelos importados com apenas `{{ .Prompt }}` devem ter seu
tratamento de mensagens verificado antes de serem usados para escrita por cenas.


### Execução autônoma pelo Django, sem coordenação do Codex

O operador autoriza uma execução; o Django seleciona as sessões pendentes e
conduz a sequência. Codex não redige, acompanha em loop, reinicia o Qwen nem
reenvia sessões automaticamente. Sessões concluídas são preservadas.
`python web/manage.py writer_generate CHAPTER_ID` executa uma única passagem.
Uma trava no banco impede duas execuções simultâneas, inclusive com objetos
antigos em memória. Falhas encerram a passagem; os clientes locais não repetem
requisições automaticamente. Uma sessão rejeitada já salva bloqueia a retomada
para revisão manual, sem apagar texto. O detector de 40 palavras entre sessões
não equivale a uma avaliação semântica de acontecimentos repetidos.
Se o processo for encerrado abruptamente, GENERATING permanece bloqueado:
verifique que o processo terminou antes de uma recuperação manual do estado.
Estas mudanças não exigem migração nem modificam sessões existentes.

### Validação e reparo de sessões (substitui a regra anterior de tentativa única)

A geração agora passa pelos gates locais antes de criar `ChapterSession.COMPLETE`.
O Django faz no máximo **três candidatos por sessão**: a geração inicial e até
**dois reparos**, com temperaturas 0,75 / 0,60 / 0,45. O contexto RAG é recuperado
uma vez por sessão; cada reparo recebe o pedido original, o último candidato e
os problemas encontrados, sem acumular todos os prompts anteriores. O orçamento
de saída reserva no mínimo 4.096 tokens e 2,5 tokens por palavra-alvo, limitado
a 32.768, para que cenas de até 1.300 palavras possam terminar naturalmente.
O pedido também limita cada frase a 60 palavras.
O cliente local aceita até 1.800 segundos por resposta por padrão, configurável
por `GAIDEN_QWEN_TIMEOUT_SECONDS`; uma expiração continua encerrando a execução
sem retry de transporte.

O idioma do projeto também é fixado no system prompt e repetido no fim do pedido.
Roteiros e bíblias podem estar em português: seus fatos orientam a cena, mas o
Luna deve redigir apenas no idioma do projeto. Para conservar espaço de saída,
a busca usa no máximo 6.000 caracteres do brief, o RAG retorna até 12.000
caracteres e a continuidade textual anterior usa até 8.000 caracteres. Os
artefatos completos permanecem no banco e no armazenamento; apenas o contexto
de inferência é limitado.

A política de transporte continua sem retries. Falha de conexão, timeout,
resposta inválida do crítico, evidência inválida ou decisão incerta interrompem
a passagem para revisão manual. Após três candidatos reprovados o capítulo fica
FAILED; nenhuma sessão posterior é gerada. Codex não acompanha nem coordena esse
processo. A escrita e o crítico de sessão usam a rota OpenAI Luna documentada no
início deste arquivo. Embeddings, memória Qwen e auditoria Phi usam clientes
separados; valores de chave-placeholder só são aceitos em endpoints loopback.

### Gate histórico de aceleração GPU/CPU (não usado pela geração Luna)

Toda geração ou reparo do Writer passa obrigatoriamente pelo mesmo gate antes
do primeiro texto. O Django solicita ao Ollama apenas o carregamento do modelo,
sem prompt narrativo, e consulta `/api/ps`. A escrita só começa quando o modelo
solicitado está carregado com GPU ativa e com pelo menos **65% na GPU** e no
máximo **35% na CPU**, distribuição verificada para o Qwen3.5 9B nesta máquina.
Endpoint indisponível, modelo ausente, `size_vram=0` ou distribuição inferior
interrompem a ordem com erro explícito. Não existe fallback para CPU, opção de
desativar o gate ou continuação flexível. O carregamento é mantido por 30 minutos
para evitar recarga entre a geração e o crítico local.

Gates mecânicos: idioma e mistura de idiomas com `langdetect==1.0.9` e seed fixa;
limites de palavras; final de frase/aspas; `finish_reason` (incluindo corte por
limite de tokens); frases longas; degeneração; repetição interna, 8-grams entre
sessões, cópia exata de 40 palavras entre sessões e de 14 palavras do corpus;
POV e vocabulário editorial proibido. Nomes franceses permitidos são removidos
somente da análise linguística. Falas curtas completas não são tratadas como
truncamento. Esses detectores são heurísticos, não prova de qualidade literária.
Documentação do detector: https://pypi.org/project/langdetect/1.0.9/.

Somente candidatos que passam na validação mecânica seguem para **uma chamada
adicional ao crítico OpenAI Luna**. O crítico verifica contrato, cada evento
obrigatório/proibido, continuidade, POV, estilo, anacronismos e exposição repetida.
Não há aprovação de eventos por sobreposição de palavras. A resposta precisa
conter todos os checks, decisões válidas e continuidade em JSON. Toda informação
não vazia da continuidade deve ter uma citação literal do candidato. Evidência
literal reduz invenções, mas não prova que a interpretação semântica está certa:
a revisão editorial humana permanece necessária.

Contratos específicos ficam fora do Git em
`<writer_storage_root>/contracts/project-ID.json`, com `schema_version: 1`,
`defaults` (campos de SceneContract) e `sessions` (chaves `capítulo:sessão`).
O perfil de The Devil in Paris define inglês, 700–1300 palavras, Watson em
primeira pessoa, ambientação em 1913 e a lista de vocabulário enviada pelo operador.
Os outros projetos usam seu próprio idioma e roteiro, sem impor Sherlock/1913.

Cada execução usa um diretório exclusivo para preservar tentativas anteriores:

```
<writer_storage_root>/project-ID/chapter NN/session NN/runs/UUID/
  contract.json
  attempts/attempt_01.txt
  attempts/attempt_01.json
  attempts/attempt_02.txt       # somente se houver reparo
  attempts/attempt_02.json
  attempts/attempt_03.txt       # somente se necessário
  attempts/attempt_03.json
  approved.txt                 # apenas após QA completo; continua DRAFT
  validation.json
  continuity.json
  manual-review.json           # em falhas, sem approved.txt
```

A tentativa textual é persistida antes do crítico. `generation_parameters` recebe
versão de QA, hash do texto validado, relatório, continuidade e caminho de auditoria.
A próxima sessão recebe a continuidade anterior. Textos gerados antes desse gate
permanecem intactos, mas não podem ser finalizados sem QA válido. O hash impede que
uma edição posterior reutilize uma aprovação de outro texto. O nome `approved.txt`
significa aprovação de QA de um rascunho; não é promoção editorial ou publicação.

Auditoria mecânica de um capítulo existente, sem modelo nem escrita no banco:

```
python web/manage.py writer_validate CHAPTER_ID --output /caminho/novo-relatorio.json
```

O relatório distingue validação mecânica de revisão semântica não executada.
Não repara, não aprova, não sobrescreve e não envia arquivos ao Drive. O mapa de
pastas do Drive permanece válido para os próximos envios autorizados; este fluxo
não instala sincronização automática nem sobrescreve a exportação antiga.

Os gates de sessão, isoladamente, não adicionam migração; o pipeline narrativo
posterior adiciona `writer.0006_chapter_narrative_memory`. Não aplicar migrações
pendentes de outros módulos apenas para instalar estes gates. Rollback: restaurar o código anterior preservando contratos,
metadados JSON e diretórios de auditoria. Nunca apagar sessões para retomar.

Sessões antigas podem ser reparadas, após uma autorização explícita do operador,
por uma única execução Django:

```
python web/manage.py writer_repair_sessions CHAPTER_ID SESSION [SESSION ...]
```

O comando ordena e deduplica os números, rejeita capítulos finalizados e exige
que cada sessão selecionada exista e esteja concluída. Antes de gerar, o texto
atual é salvo como `original.txt` no mesmo diretório imutável da tentativa. A
linha no banco só é atualizada depois que o candidato passa pelo gate mecânico e
pelo crítico local; o hash do original fica em `repaired_from_sha256`. Uma falha
mantém o texto atual da sessão, marca o capítulo para revisão e impede o início
das sessões seguintes. O comando não é agendado e não inicia outro capítulo.
