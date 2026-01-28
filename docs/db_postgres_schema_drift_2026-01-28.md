# Postgres schema drift repair (2026-01-28)

## Contexto
O banco `gaiden` estava com o histórico de migrations marcado como aplicado, mas o schema real estava divergente
(colunas ausentes). Isso quebrou comandos como `export_frontmatter` com erro de coluna inexistente.

## Sintoma
- `psycopg2.errors.UndefinedColumn: column edition.copyright_text does not exist`
- `showmigrations` indicava tudo aplicado (schema drift / migration history inconsistente)

## Diagnóstico
- Confirmado via `\d editorial_edition`, `\d editorial_pipelineartifact`, `\d pipeline_bookeditiontemplate`
- Colunas faltantes, apesar das migrations constarem como aplicadas

## Repair (cirúrgico)
Aplicado `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` com defaults compatíveis com as migrations históricas.
(Exemplo — adapte para os nomes/tabelas reais do seu ambiente:)

```sql
-- editorial_edition
ALTER TABLE editorial_edition
  ADD COLUMN IF NOT EXISTS copyright_text text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS editorial_name varchar(120) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS edition_copyright_holder varchar(120) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS language_variant varchar(20) NOT NULL DEFAULT '';

-- editorial_pipelineartifact
ALTER TABLE editorial_pipelineartifact
  ADD COLUMN IF NOT EXISTS status varchar(16) NOT NULL DEFAULT 'OK',
  ADD COLUMN IF NOT EXISTS sha256 varchar(64) NOT NULL DEFAULT '';

-- pipeline_bookeditiontemplate
-- (use os tipos conforme as migrations originais do app)
-- ALTER TABLE pipeline_bookeditiontemplate ...
```

## Resultado
- Schema alinhado ao código/migrations
- `export_frontmatter` voltou a funcionar

## Prevenção
- Evitar `migrate --fake` sem revisão.
- Padronizar env local (`PGPORT=5433`) para reduzir erros de conexão.
- Backups rápidos (schema/dados) antes de repairs manuais.
