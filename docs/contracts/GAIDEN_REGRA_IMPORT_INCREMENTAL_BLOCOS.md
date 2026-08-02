# Gaiden — regra de importação incremental e retomada por blocos

Este contrato permite importar uma edição em lotes, interromper o trabalho e
retomá-lo na primeira sequência realmente ausente. Ele é genérico: códigos de
obra e idiomas são dados do manifesto, nunca regras de aplicação.

O contrato JSON normativo está em
`gaiden_incremental_block_manifest.schema.json`. A implementação executável
está em `pipeline.services.incremental_import` e
`pipeline.services.incremental_export`.

## Invariantes

1. A identidade natural de conteúdo é `edition_id + block_id + version`.
2. O mesmo bloco, versão e SHA-256 é idempotente.
3. Hash diferente com versão igual ou menor é conflito e não sobrescreve.
4. Hash diferente com versão maior cria uma versão e preserva a anterior como
   `SUPERSEDED`.
5. Cada bloco e seu evento são persistidos na mesma transação.
6. Falha em um lote da confirmação Automated reverte integralmente o lote
   atual, sem desfazer lotes anteriores já confirmados.
7. O cursor é o maior prefixo contíguo confirmado; arquivos posteriores a uma
   lacuna não avançam a retomada.
8. A edição só fica `APPROVED` quando todas as sequências esperadas existem e a
   versão corrente de todas elas está `APPROVED`.
9. Arquivos sem entrada no manifesto não são importados.
10. `import-ack.json` é o último arquivo publicado no reenvio.

## Identidades

- `work_id`: obra abstrata, compartilhada entre idiomas;
- `edition_id`: `<book_code>:<locale>:<edition_version>`;
- `block_id`: posição editorial estável, por exemplo
  `<edition_id>:p1:c7:b4` ou `<edition_id>:appendix:b1`.

## Retomada

```text
last_contiguous_sequence = maior N com todas as sequências 1..N confirmadas
next_sequence = last_contiguous_sequence + 1
```

Se 28–40 estiverem persistidos e 27 estiver ausente, `next_sequence` permanece
27. O total de arquivos nunca substitui esse cálculo.

## Importação

O importador valida estrutura, ordem, nomes seguros, ausência de symlinks,
UTF-8, conteúdo não vazio, tamanho e SHA-256. Cada versão fica imutável no
histórico. No serviço incremental isolado, bloco e evento usam a mesma
transação. Na confirmação composta do Automated, todos os blocos do lote,
catálogo, frontmatter e eventual corpo participam de uma transação externa
única. Assim, um lote atual falha fechado, enquanto lotes anteriores permanecem
retomáveis.

A chave de idempotência de execução é
`job_id + manifest_sha256 + import_attempt`. Uma nova tentativa deliberada usa
outro número de tentativa; os blocos continuam idempotentes pelo hash.

## Reenvio

Somente versões correntes cujo hash ou estado mudou desde a última exportação
são reenviadas. O publicador usa um nome temporário, confere bytes e SHA-256,
move para o nome definitivo e publica os controles nesta ordem:

1. `resume-state.json`;
2. `errors.json`;
3. `manifest.json`;
4. `import-ack.json`.

Consulte `docs/runbooks/incremental_editorial_blocks.md` para interface,
comandos e implantação.
