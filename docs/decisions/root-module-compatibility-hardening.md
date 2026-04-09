# Root Module Compatibility Hardening

## Decisão

- `gaiden/ingest.py`
- `gaiden/normalize.py`
- `gaiden/translate.py`
- `gaiden/openai_client.py`
- `gaiden/secrets.py`

permanecem apenas como wrappers de compatibilidade.

## Regra

- implementação principal vive em `gaiden/application/*` ou `gaiden/infrastructure/*`
- código novo não deve depender dos módulos raiz históricos como centro de verdade
- o módulo `Collection` é proibido de depender deles como implementação principal

## Motivo

- reduzir ambiguidade entre arquitetura antiga e nova
- impedir regressão estrutural por imports de conveniência
