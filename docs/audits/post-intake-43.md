# Issue #43 — primeira entrega de integridade do pós-Intake

## Estado da entrega

**PARCIAL / NÃO APROVADA para encerrar a Issue #43.**

Base aprovada: `main`, commit `b04e3fad01717f3fd985c17e0c5bd84f424ae53d`.
Branch: `fix/post-intake-integrity-43`. Classe de risco: comportamento da
aplicação, com testes diretos e documentação. Não substitui PRs anteriores.

O operador confirmou trabalhar a partir da main e manter pendente a integração
com o Django em execução. O checkout operacional está em
`feature/author-first-library`, com numerosas alterações locais. Foi preservado:
nenhum arquivo, banco, manuscrito, imagem ou configuração dele foi modificado
nesta entrega. O desenvolvimento usa um worktree separado.

AGENTS, README, arquitetura, política de segurança e governança foram lidos.
Os dois últimos documentos não existem no checkout operacional, mas existem
na main aprovada. A Issue #43 foi consultada integralmente. As PRs abertas
#40, #41 e #42 não foram retargetadas nem integradas; os últimos checks da
pilha operacional incluíam falhas de Main CI e arquitetura.

## Arquitetura e diagnóstico confirmado

Na main, `web/pipeline/views.py` coordena aprovação e produção;
`pipeline.services.md_quality` prepara candidatos e promove o texto final;
`editorial.frontmatter.build_context` resolve os metadados;
`editorial.kdp_mode` coordena Pandoc e EPUBCheck. Os serviços existentes foram
mantidos, sem novo pipeline, modelos de banco ou migrations.

No checkout operacional, o fluxo premium é diferente: `EditionRenderer`
produz XHTML e `editorial.services.book_preview` constrói a navegação.
`_attach_saved_illustrations` associa imagens por números no nome e preenche
capítulos restantes por ordenação. Esse mecanismo viola o contrato semântico
solicitado, mas não existe na main. Sua correção depende de integração revisada,
não de copiar silenciosamente a implementação local para esta branch.

O refinamento operacional EN-US usa `refine_en_us_2026`, delega ao executor
`modernize_en_us_2026` e carrega contratos pelo registry/prompt builder. Há
rotas específicas FR. O executor define modelo por contrato, com fallback
de código `gpt-5.4`; isso não comprova qual modelo está configurado no host.
Na main, os entrypoints de compatibilidade `refine_en_2025` e
`polish_en_2025` são diferentes; o segundo contém fallback `gpt-4o`.
Nenhum modelo foi chamado, nenhum prompt foi alterado e nenhuma chamada paga
foi feita. Não foi substituído o motor de tradução para reconciliar essas bases.

Defeitos reproduzidos nesta base:

1. Regex de QA removia nomes de fontes/editoras e expressões de copyright
   inclusive quando eram conteúdo legítimo de uma carta, diálogo ou referência.
2. Detecção de sumário por ocorrência de palavra podia apagar texto narrativo.
3. Leitura/escrita textual alterava CRLF e quebras finais sem intervenção
   editorial aprovada.
4. Aprovação normal e de outro idioma sobrescreviam `BOOK.MD_FINAL`; não havia
   proteção contra concorrência ou repetição e um arquivo vazio era aceito.
5. O exportador inventava o título de um livro específico quando o título
   estava vazio e não utilizava o mesmo contexto de autor do frontmatter.
6. EPUBCheck priorizava `BOOK.EPUB3` antigo, ignorava a saída `BOOK.epub` e
   não vinculava evidência ao hash dos bytes validados.
7. Exit code zero era suficiente para aceitar warnings ou evidência ausente;
   o build completo não executava esse gate antes de continuar.

## Correções e contratos entregues

- QA torna-se uma análise conservadora: preserva bytes e registra ocorrências
  com offsets para revisão. Retorna `REVIEW_REQUIRED`, nunca nota literária
  ou certificação semântica fictícia. O botão QA já estava suspenso na main
  e não foi reativado sem implementar a revisão completa.
- `gaiden.manuscript_artifacts` publica arquivos completos com link atômico
  de arquivo temporário, sem substituir destinos diferentes. Repetição com
  bytes iguais é idempotente; UTF-8 inválido e texto vazio são rejeitados.
- Snapshots são armazenados em `.manuscript_versions/<sha256>.md`, junto aos
  artefatos operacionais. São imutáveis por esta API; não são um mecanismo de
  permissões do sistema operacional. A mesma proteção cobre aprovação de
  outro idioma. Não há promoção automática baseada no número de imagens.
- O exportador usa título, subtítulo, autor, idioma e publisher do contexto
  existente do frontmatter. Título/autor/idioma ausentes bloqueiam exportação.
  Capitalização, pontuação e quebras do subtítulo não são reescritas.
- `gaiden.epub_validation` executa a ferramenta real, com timeout, sem shell,
  e exige relatório JSON reconhecido. Registra versão, timestamps, duração,
  exit code, mensagens, contagens e hashes antes/depois.
- `FAIL` representa erros ou bytes alterados; `BLOCKED` representa ferramenta
  ou evidência indisponível; `REVIEW_REQUIRED` representa warnings não tratados;
  `PASS` exige execução comprovada sem erros/warnings. Não há bypass de teste
  em produção. Testes unitários usam mocks da fronteira de subprocesso.
- Relatórios completos ficam em `.epubcheck_reports/<run_id>.json`; o sidecar
  `<arquivo>.epubcheck.json` é atualizado atomicamente. Relatórios anteriores
  permanecem disponíveis. Nenhum EPUB/relatório operacional entra no Git.
- Havendo mais de um EPUB legado, a validação exige `artifact_path` explícito,
  dentro da pasta da edição. O build completo passa seu resultado exato ao
  validador antes de gerar PDF. Um alias antigo não é excluído ou substituído.

## Evidências de teste

Antes das correções, a primeira suíte de preservação descobriu 6 testes e
produziu 10 falhas de asserção em subcasos e 1 erro de API ainda ausente.
A suíte inicial de EPUB descobriu 7 testes e falhou nos 7. Não foram relaxadas
asserções para obter aprovação.

Testes específicos cobrem referências, cartas, diálogos, poemas, notas,
tabelas, idiomas, UTF-8, vazio, CRLF, idempotência, candidatos do operador,
aprovação concorrente, prioridade do texto revisado, metadados, hashes,
warnings, erros, timeout, ferramenta ausente, relatório inválido, seleção de
arquivo exato e bloqueio do build completo.

```bash
python web/manage.py test pipeline.test_post_intake_integrity editorial.test_epub_validation --noinput
GAIDEN_RUN_EPUB_TOOLS=1 python web/manage.py test editorial.test_epub_real_tools --noinput
python web/manage.py test editorial pipeline writer --noinput
```

A integração real cria três EPUBs pequenos EN/PT-BR/FR a partir de fixtures
sintéticas, executa Pandoc e **EPUBCheck 4.2.6**, verifica XML, ZIP/mimetype,
título/autor no OPF e hash inalterado do manuscrito de entrada. Não utiliza
obras do operador. Esses três EPUBs passaram na ferramenta real; isso não
constitui inspeção visual, qualidade literária ou homologação KDP.

PostgreSQL 16 com pgvector foi provisionado em contêiner descartável separado,
em porta loopback exclusiva. `check`, migration drift, migrations limpas e
`migrate --check` passaram. A primeira execução da suíte protegida passou
com 83 testes; a execução final ampliada passou com **93 testes**, incluindo
a integração real com Pandoc/EPUBCheck habilitada.
Higiene do repositório, scanner de segredos e compilação Python passaram.

O audit das dependências da base encontrou **65 registros de vulnerabilidades
em 4 pacotes**. As dependências não foram alteradas; remediação é classe de
risco separada, a reconciliar também com a PR #39. Esse resultado impede
recomendação de merge. O status do CI remoto deve ser conferido no SHA da PR;
resultados locais não são apresentados como CI remoto.

## Limites e pendências da missão completa

| Área da Issue #43 | Situação nesta entrega |
| --- | --- |
| Preservação do texto na análise/promoção | Implementada e testada no escopo descrito |
| Contexto de metadados compartilhado com Pandoc | Parcial; não é cadastro único de todos os formatos |
| EPUBCheck real e evidências | Implementado no validador explícito e no build completo |
| Quinze gates e bloqueio universal de finalização/download | Pendente; standalone export ainda gera candidatos |
| Associação semântica de ilustrações e âncoras estáveis | Pendente da integração aprovada do renderer ativo |
| Capa real versus metadados/OCR/inspeção multimodal | Pendente; não se presume correspondência |
| Copyediting e refinamento com avaliador independente | Pendente; nenhuma nota de IA foi fabricada |
| Fidelidade semântica, chunks e revisão comparativa | Pendente; preservação de bytes não substitui esse gate |
| Rubricas, pesos, perfil editorial, calibragem humana | Pendente |
| Layout premium, CSS e navegação em leitores | Pendente da integração e inspeção visual |
| Nome final canônico e metadados completos PDF/EPUB | Pendente; nomes legados foram preservados por compatibilidade |
| Reexportação completa, publicação concorrente e download stale | Pendente fora da promoção de manuscritos/validação explícita |
| Rollback integrado à interface e retomada transacional total | Pendente; snapshots e bloqueio de sobrescrita são parciais |
| Golden master original/corrigido e Kindle Previewer | Final corrigido registrado e inspecionado em 2026-11-26; comparação automatizada e Kindle Previewer pendentes |
| Autocorreção com orçamentos de LLM | Pendente; não se criou outro motor textual |

A existência de um sidecar `PASS` não deve ser tomada como autorização
editorial final. Revalidar sempre após mudança dos bytes. Ainda não existe
nesta base um consumidor universal que invalide downloads por hash.

## Referência editorial aprovada recebida em 2026-11-26

O operador designou `The_Hound_of_the_Baskervilles_Immersive_Final.epub`
como final corrigido de `book_0008` (também referido como `book_008`) e
referência para os próximos livros. Esta aprovação editorial vem do operador;
não é inferida de notas de IA ou do nome do arquivo.

- Tamanho observado: 10.313.312 bytes.
- SHA-256: `a6b5a918d4003bfb54446790bb57195d33e45ea72b04a73e6d41edf1b2ecffb9`.
- EPUBCheck 4.2.6 real: exit code 0, zero fatais, erros e warnings.
- OPF: 21 documentos no spine, incluindo 15 capítulos com texto e uma
  ilustração por capítulo, além da capa e front/backmatter.
- Os capítulos contêm 1.578 elementos `p`; isso é uma contagem estrutural,
  não certificação de completude contra outro manuscrito.
- As figuras estão dentro de `chapter-body`, com classe
  `chapter-illustration`, em posições específicas do texto. Não há XHTMLs
  separados de abertura. Este final aprovado não deve ser convertido
  automaticamente ao antigo modelo de uma página de abertura por capítulo.

A sequência dos assets nos capítulos 1–15 é:
`01, 02, 04, 03, 06, 05, 07, 08, 10, 14, 09, 15, 11, 12, 13` (arquivos JPG).
É evidência deste livro, **não uma tabela a codificar no motor**. Por exemplo,
ordenar arquivos numericamente trocaria as associações aprovadas dos
capítulos 3 e 4. Também não basta corrigir a sequência: a posição dentro do
capítulo precisa ser preservada. Os capítulos 2 e 11 têm a figura ao final
do corpo; os demais têm blocos textuais depois dela.

O manifesto local, fora do Git, registra o caminho do original, hash do EPUB,
ordem do spine, hashes dos XHTMLs/CSS/assets, bloco da figura e hashes do texto
normalizado dos blocos vizinhos. Está em
`references/book_0008/manifest.json` no diretório local da tarefa do operador,
fora deste repositório; o relatório integral está em `epubcheck.json` na
mesma pasta. Não contém
cópias do texto narrativo nem das imagens. Índices e hashes de vizinhos são
evidência deste snapshot, não substitutos de IDs semânticos persistentes.

Contrato para a integração futura:

1. Ler a ordem do spine e os vínculos explícitos imagem/capítulo/âncora;
   nunca reconstruí-los por ordenação de filenames ou posição no upload.
2. Preservar texto, frontmatter e posição editorial das figuras; vínculo
   ausente ou ambíguo requer revisão, não associação automática por número.
3. Usar este final como caso de referência, sem impor seu número de capítulos,
   quantidade de imagens, nomes, idioma ou mapeamento a outras obras.
4. Cobrir em fixtures sintéticas nomes fora de ordem, imagem no meio/fim do
   texto, múltiplas imagens e vínculo ausente, antes de integrar o renderer.
5. Conferir o hash da referência antes de comparações; alterações de CSS,
   texto ou recompressão exigem comparação explícita, não aceitação silenciosa
   de um novo golden master.

Escopo da etapa de registro da referência: documentação e evidências locais
somente. O arquivo
original permaneceu inalterado. Nenhuma importação, alteração de banco,
substituição de artefato operacional ou mudança do renderer foi executada.
A integração ao Django continua pendente conforme decisão do operador.
Não se executou inspeção visual em leitores/Kindle Previewer nem validação
semântica independente das cenas. A aprovação técnica do EPUBCheck não
substitui essas verificações. Não há ainda teste automatizado consumindo este
manifesto no pipeline.

### Finalização operacional posterior, autorizada pelo operador

Após o registro da referência, o operador solicitou restabelecer o Intake e
finalizar `book_0008`. O Django foi iniciado pelo launcher existente, com
PostgreSQL. A biblioteca `/intake/` e o painel `/intake/automated/` foram
verificados no navegador, incluindo a listagem de pastas do Drive. Isso
confirma disponibilidade dessas telas; não é uma nova validação ponta a ponta
da importação ou da associação semântica de imagens.

A edição 90 já tinha status DONE, mas o build 127 apontava para outro EPUB.
O serviço operacional existente `import_final_epub` registrou o arquivo
aprovado, com verificação de tamanho e hash e nova execução real do EPUBCheck:

- Build 128, versão 3, locale `en-US`, status `DONE`, `EPUBCHECK_PASSED`.
- SHA-256 e tamanho iguais aos da referência descrita acima.
- Destino relativo ao armazenamento operacional:
  `builds/book_0008/en-US/The_Hound_of_the_Baskervilles_Immersive_Final.epub`.
- Eventos `FINAL_ARTIFACT_IMPORTED` e `FINALIZED_PROJECT_SYNCED` registrados.
- Pipeline em DONE e presença confirmada em `/finalized-projects/`.
- Build anterior substituído no histórico, sem apagar seu arquivo. O EPUB
  aprovado foi copiado sem regenerar texto, imagens ou layout.

O vínculo com o manuscrito oficial já registrado foi preservado e seu hash
conferido antes da operação; isso não afirma igualdade textual desse
manuscrito com o EPUB corrigido externamente.

Essa operação não alterou código nem aplicou migrations. Não integra a PR
ao checkout operacional: a integração de código permanece pendente. O commit
de documentação não reproduz nem reverte dados no PostgreSQL. EPUB, relatórios,
manifesto e registros operacionais ficam fora do Git. A recuperação deve usar
o histórico de builds e o fluxo de aprovação existente, nunca um checkout
de Git como se fosse restauração do banco.

## Operação, recuperação e rollback

Uma tentativa de trocar um final já existente por texto diferente agora falha
sem alterar o aprovado. O candidato permanece disponível para comparação.
Não apague o final para contornar a proteção. A substituição de revisões exige
reconciliação explícita do operador; o fluxo completo de revisão/rollback deve
ser entregue separadamente, sem declarar essa pendência resolvida.

Para recuperação após perda de um final, confira o digest do snapshot
selecionado e restaure para um destino novo usando `preserve_manuscript`.
Não escolha uma revisão por ordem de arquivos ou mtime. A restauração não
aprova qualidade nem deve sobrescrever um final diferente. Relatórios antigos
continuam armazenados; após restauração, execute as validações novamente.

Para rollback de código, reverta o commit desta PR na branch de revisão.
Não há migration a reverter. Não remova snapshots, reports ou artefatos do
operador. Nenhuma migration foi aplicada ao banco operacional. Não houve
deploy ou cherry-pick para a branch que serve o Django atual.

Impacto: hashing e snapshots custam I/O e espaço por conteúdo novo; EPUBCheck
acrescenta uma execução limitada a 120s mais até 30s para versão. Repetição de
promoção não duplica snapshots. Não há consumo de tokens/custos de LLM nesta
entrega. Armazenamento sem suporte a hard links falha de modo conservador;
arquivos em destinos divergentes exigem revisão, não fallback destrutivo.
