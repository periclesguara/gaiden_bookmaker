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
| Golden master original/corrigido e Kindle Previewer | Não utilizados; não certificados |
| Autocorreção com orçamentos de LLM | Pendente; não se criou outro motor textual |

A existência de um sidecar `PASS` não deve ser tomada como autorização
editorial final. Revalidar sempre após mudança dos bytes. Ainda não existe
nesta base um consumidor universal que invalide downloads por hash.

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
