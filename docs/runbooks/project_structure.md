# Project Structure

## Regra de ouro

- `data/`: verdade fisica dos artefatos.
- `gaiden/`: core Python e cerebro do pipeline.
- `web/`: interface Django e orquestracao visual.
- `scripts/`: operacoes auxiliares.
- `docs/`: governanca, auditoria e decisoes.
- `legacy/`: codigo antigo isolado.
- `docker/`: ambiente e servicos.
- `assets/`: templates, identidade visual e referencias.

## Storage canonico

Use `GAIDEN_DATA_ROOT` para apontar para o storage oficial. Por padrao, ele deve resolver para `./data`.

Diretorios obrigatorios:

- `data/raw`
- `data/preprod`
- `data/normalized`
- `data/md`
- `data/chunks`
- `data/translated`
- `data/frontmatter`
- `data/images`
- `data/covers`
- `data/editions`
- `data/builds`
- `data/exports`
- `data/collections`
- `data/db`
- `data/tmp`

## O que nao deve voltar

- `sqlite3` na raiz.
- `db.sqlite3` na raiz.
- `web/data`.
- `exports/` na raiz.
- Artefatos EPUB/PDF fora de `data/`.

## Auditoria

Rode:

```bash
python scripts/audit/audit_project_tree.py
python scripts/audit/audit_sqlite_residue.py
```

Os relatorios saem em `docs/audit/`.

## Limpeza segura

Para simular:

```bash
python scripts/ops/safe_repo_cleanup.py
```

Para aplicar:

```bash
python scripts/ops/safe_repo_cleanup.py --apply
```

O script move residuos para `backups/repo_cleanup`; nao apaga definitivamente.
