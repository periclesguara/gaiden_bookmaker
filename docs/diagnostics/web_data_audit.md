# web/data Audit

Date: 2026-04-08

## Encontrado

- `web/data/README.md`
- `web/data/db/gaiden.sqlite3`
- `web/data/builds/`
- `web/data/frontmatter/`
- `web/data/translated/`

## Classificação

- `README.md`: documentação de runtime antigo
- `db/gaiden.sqlite3`: resíduo legado / storage paralelo de risco
- `builds/`: artefatos antigos espelhados do storage canônico
- `frontmatter/`: passivo legado de compatibilidade
- `translated/`: passivo legado de compatibilidade / risco de divergência

## Decisão

- `data/` permanece como storage root canônico
- `web/data/` fica deprecado
- novo código não deve escrever nem depender de `web/data/`
- `Collection` é proibida de usar `web/data/` em qualquer etapa

## Próximo passo operacional

- validar se `web/data/db/gaiden.sqlite3` ainda é necessário para algum fluxo manual
- se não for, migrar/remover em fase de limpeza controlada
- migrar qualquer leitura manual restante de `builds/`, `frontmatter/` e `translated/` para `data/`
