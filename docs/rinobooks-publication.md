# Metadados, SEO e entrega de rascunho à RinoBooks

## Escopo e fluxo

A etapa **Metadados e SEO** pertence a cada `editorial.Edition` e fica entre a
finalização editorial e a exportação/publicação:

1. o operador finaliza o texto editorial;
2. salva, revisa e valida o registro canônico `EditionMetadata`;
3. o Gaiden libera a exportação quando não há erros críticos;
4. para gerar o manifesto ou enviar, o Gaiden exige capa, EPUB e EPUBCheck;
5. uma ação explícita envia manifesto + capa + EPUB via HTTPS;
6. o receptor deve criar ou atualizar exclusivamente um `DRAFT`;
7. revisão humana, publicação, sitemap e indexação continuam na RinoBooks.

Salvar rascunho aceita campos incompletos. Validar não publica e não chama a
rede. Qualquer edição posterior aos dados validados retorna o registro ao
estado `DRAFT` local.

## Regras de validação

Erros críticos incluem identificadores ausentes, `slug` ausente, idioma
regional ausente, categoria/subcategoria ausentes, descrição SEO vazia ou
genérica, metadados essenciais do catálogo ausentes e dados de direitos
incompatíveis com o tipo da obra. Obras `PUBLIC_DOMAIN` e `DERIVATIVE` também
exigem ano da obra-base e fonte consultada. Preço negativo é inválido.

`slug` é normalizado para minúsculas e hífens; `edition_code` é normalizado
para maiúsculas. Ambos possuem restrição única no banco. Enquanto vazios em
rascunhos, são armazenados como `NULL`, permitindo que edições existentes
sejam migradas sem valores fabricados.

Os limites de 45–60 caracteres para o título SEO e 120–160 para a descrição
SEO geram avisos não bloqueantes. A interface exibe contadores e os avisos do
serviço de validação.

## Contrato JSON v2

O contrato adiciona `contract_version`, `edition_code`, `status` e
`storefront`, sem remover as chaves legadas `text_source`, `md_files`, `build`,
`export` e `export_user`. Exemplo representativo:

```json
{
  "edition_id": 1,
  "book_code": "BOOK_0001",
  "edition_code": "BOOK_0001-ENUS-EPUB-01",
  "language": "en-US",
  "edition_type": "EPUB",
  "imprint_name": "RinoBooks",
  "collection_name": "Stoic Classics",
  "text_source": {
    "canonical_name": "merge_polish.txt",
    "canonical_path": "/external/runtime/path/merge_polish.txt",
    "pipeline_step": "polish",
    "pipeline_job_id": 10,
    "pipeline_filepath": "/external/runtime/path/merge_polish.txt"
  },
  "md_files": {
    "pre_qa": null,
    "qa": null,
    "final": "/external/runtime/path/BOOK.MD_FINAL"
  },
  "build": {
    "path": "/external/runtime/path/BOOK.BUILD.MD",
    "frontispiece_template": "frontispiece.md.j2",
    "copyright_template": "copyright.md.j2",
    "about_edition_template": "about_edition.md.j2",
    "about_contributor_template": "about_contributor.md.j2"
  },
  "export": {
    "epub": "/external/runtime/path/BOOK.EPUB3",
    "pdf": null,
    "epubcheck_status": "pass"
  },
  "export_date": "2026-08-13T00:00:00Z",
  "export_user": "operator",
  "status": "DRAFT",
  "contract_version": "2.0",
  "storefront": {
    "slug": "the-enchiridion-modern-english-edition",
    "title": "The Enchiridion",
    "subtitle": "Modern English Edition",
    "original_title": "Enchiridion",
    "author": {
      "first_name": "Epictetus",
      "last_name": "",
      "pseudonym": ""
    },
    "description": "Descrição comercial completa.",
    "short_description": "Descrição curta para cards.",
    "seo_title": "The Enchiridion: Modern English Edition | RinoBooks",
    "seo_description": "A clear modern English adaptation of Epictetus' practical Stoic manual.",
    "keywords": ["Epictetus", "Stoicism", "Stoic philosophy", "Enchiridion"],
    "primary_category": "Philosophy",
    "subcategory": "Stoicism",
    "theme": "Practical philosophy",
    "target_audience": "Readers of classical philosophy",
    "cover_alt": "Cover of The Enchiridion by Epictetus",
    "isbn": "",
    "rights_statement": "Domínio público Public-domain source. Modern English adaptation.",
    "price_cents": 1990,
    "currency": "BRL",
    "hotmart_url": "",
    "lulu_url": "",
    "edition_number": 1,
    "publication_year": 2026,
    "original_language": "grc",
    "release_date": "2026-09-01",
    "rights": {
      "work_type": "PUBLIC_DOMAIN",
      "base_work_year": 125,
      "consulted_source": "Verified public-domain source.",
      "legal_basis": "Public-domain source.",
      "edition_nature": "Modern English adaptation",
      "editorial_modifications": "Modernized language and original editorial notes.",
      "authorized_territories": "Worldwide where public-domain status applies.",
      "blocked_territories": "Territories pending review.",
      "evidence": "Rights review worksheet."
    },
    "sample": {
      "title": "Opening",
      "content": "Some things are in our control..."
    },
    "promotional_images": ["https://assets.example.invalid/enchiridion.jpg"]
  }
}
```

Em edições ainda sem `EditionMetadata`, a serialização continua possível
para consumidores internos legados: as estruturas antigas permanecem e os
novos campos recebem valores vazios ou derivados. A exportação/publicação
operacional, contudo, falha até que os metadados sejam preenchidos e validados.

## Requisitos do receptor RinoBooks

O código do receptor não faz parte deste repositório e não foi modificado. Antes
de promover esta integração, o receptor de `POST /api/gaiden/editions` precisa
ter testes de contrato que confirmem exatamente:

- multipart com os campos `manifest`, `cover` e `epub`;
- aceitação das chaves legadas e das adições do contrato `2.0`;
- `author` como objeto com `first_name`, `last_name` e `pseudonym` (o draft
  emissor anterior do PR #26 enviava uma string);
- idiomas regionais `pt-BR`, `en-US`, `en-GB`, `fr-FR`, `de-DE` e `it-IT`;
- persistência de `edition_code`, SEO, categorias, direitos, preço, links,
  amostra e imagens promocionais; campos ainda não suportados podem ser
  ignorados apenas se isso for explicitamente aceito pelo produto;
- rejeição de `edition_code` ou `slug` duplicado conflitante e idempotência de
  reenvio do mesmo pacote;
- criação/atualização sempre em `DRAFT`, ignorando qualquer tentativa de
  status público, e resposta JSON com `edition_id` inteiro e `status: DRAFT`.

O Gaiden rejeita respostas com outro status, mas essa rejeição não desfaz uma
mutação incorreta no servidor. Portanto, o fail-closed também deve existir no
receptor antes do uso em produção.

## Configuração e operação

Configure somente no ambiente local ou secret manager aprovado:

```text
RINOBOOKS_PUBLISH_URL=https://your-rinobooks-site.example
RINOBOOKS_PUBLISH_TOKEN=
```

A URL deve ser HTTPS. O token não entra no manifesto nem em logs. CI deve
simular EPUBCheck e a chamada HTTP.

Pela interface, use **Gerar manifesto DRAFT** para o preflight sem rede e
**Enviar à RinoBooks como DRAFT** para a entrega explícita. O equivalente de
linha de comando é:

```bash
python web/manage.py publish_to_rinobooks --edition-id <edition_id>
```

## Migração, dados persistentes e recuperação

`editorial.0024_editionmetadata` cria somente a nova tabela
`edition_metadata`. Não altera nem remove colunas existentes, não faz backfill
e não toca artefatos de runtime. Antes de aplicar em ambiente persistente:

1. obtenha backup verificado do PostgreSQL;
2. restaure-o em clone descartável;
3. revise `python web/manage.py migrate --plan`;
4. aplique a migração e execute escrita ORM representativa de `EditionMetadata`;
5. confirme o plano de rollback abaixo.

Rollback de código pode manter a tabela sem uso. Reverter o schema para
`editorial.0023` remove a tabela nova e, portanto, destrói metadados já
capturados; exporte-os e confirme o backup antes de executar:

```bash
python web/manage.py migrate editorial 0023
```

Manifestos, EPUBs, capas e imagens promocionais continuam sendo artefatos de
runtime externos ao Git.
