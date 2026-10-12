# Official Flow

## Fluxo oficial

1. `ingest`
2. `normalize`
3. `fix_text` / limpeza estrutural aplicável
4. `chunk`
5. `translate`
6. `refine`
7. `build`

## Entry points oficiais

- Web/Django: views acionando serviços em `pipeline.services` e helpers centrais em `gaiden.*`
- CLI Django: comandos em `web/pipeline/management/commands/`
- CLI Gaiden: `python -m gaiden.interfaces.cli` ou `gaiden-cli`

## CLI mínima atual

- `gaiden-cli diagnostics`
- `gaiden-cli env-check`
- `gaiden-cli normalize <input> [--output ...]`
- `gaiden-cli ingest-extract <input> [--output ...]`

## Compatibilidade transitória

- Scripts em `scripts/` continuam existindo enquanto não houver CLI oficial equivalente para todos os casos.
- Scripts recorrentes devem migrar gradualmente para entrypoints oficiais.
- `legacy/` é passivo controlado, não fonte preferencial de imports.
