# Path Hardcodes

Date: 2026-04-08

## Críticos já tratados

- hardcodes absolutos de repo root nos scripts Sherlock
- resolução duplicada de `.gaiden_secrets`
- parte da resolução manual de `data/...` em web/services e views

## Ainda presentes

- `web/pipeline/tests.py` ainda contém vários `Path("data")` e `settings.BASE_DIR.parent / "data"`
- partes de `web/pipeline/views.py` ainda resolvem caminhos legados fora de `storage.py`

## Política

- código novo deve usar `gaiden.infrastructure.storage`
- paths críticos não devem ser concatenados manualmente em views/scripts
