# Gaiden Release Checklist (rápido e infalível)

## Ambiente / DB
- [ ] `source backups/db/local_pg_env.sh` (porta/db corretos)
- [ ] `cd web && python manage.py check`
- [ ] `python manage.py showmigrations editorial pipeline | tail -n 30` (sem pendências)
- [ ] (Opcional) `psql ... -c "\d editorial_edition"` (colunas críticas presentes)

## Pipeline / Frontmatter
- [ ] `python manage.py export_frontmatter --book-code book_0001 --language es`
- [ ] Conferir headings e acentos: `rg -n "edicion|edición" -S data/frontmatter/book_0001/es`
- [ ] Validar ausência de Jinja leftover no output: `rg -nF "{{" data/frontmatter/book_0001/es || true`

## Runner end-to-end (edição teste)
- [ ] translate → merge → polish → txt_to_md → md_quality (1 edição)
- [ ] conferir markers/contagens
- [ ] salvar logs em `docs/gaiden_pipeline_log.md` (se necessário)

## Git hygiene
- [ ] `git status` limpo
- [ ] commit(s) pequenos e auditáveis
- [ ] `git push` no branch
- [ ] (Opcional) tag stable da entrega
