# Registro de tentativa: retirada dos modelos locais do Writer

Data: 2026-09-16

## Escopo

Foi solicitada a retirada completa de Phi e Qwen do módulo Writer, incluindo
artefatos locais e branches/worktrees locais associados. A retirada remota de
Pull Requests não foi executada: não houve alteração no GitHub.

## Resultado técnico

- Phi deixou de ser um provedor operacional do Writer; a auditoria lógica e a
  revisão semântica passam pelo gerador OpenAI do Writer.
- Qwen deixou de ser um provedor operacional do Writer; memória narrativa,
  embeddings e geração local devem ser removidos, sem apagar manuscritos ou
  dados editoriais.
- O estado desta tentativa permanece **falha parcial**: ainda há referências
  em `docs/writer-workflow.md` e em testes legados, que precisam de uma rodada
  dedicada de saneamento sem apagar a cobertura comportamental.

## Alvos locais identificados

Os seguintes alvos foram identificados para remoção, sujeitos à confirmação
final por listagem após a alteração:

- `/home/periclesguara/gaiden-models/writer/qwen3.5-9b-q4_k_m.gguf`
- `/home/periclesguara/gaiden-models/writer/qwen3.5-9b-q4_k_m.gguf.sha256`
- `/home/periclesguara/gaiden-models/embedding/qwen3-embedding-0.6b-q8_0.gguf`
- `/home/periclesguara/.config/gaiden/ensure-ollama-gpu.sh`
- `/home/periclesguara/.config/gaiden/ollama_gpu_proxy.py`

Também foram identificadas branches/worktrees locais com `qwen` no nome. Elas
serão removidas apenas localmente, com `git worktree remove` e exclusão da
branch local correspondente; branches remotas e PRs não serão alterados.

Nesta tentativa, as quatro branches/worktrees locais com `qwen` no nome foram
removidas. Os cinco artefatos locais listados foram movidos para a Lixeira do
usuário em `.local/share/Trash/files/gaiden-model-retirement-2026-09-16/`.

## Recuperação e auditoria

Esta remoção é intencional e não deve ser recuperada automaticamente. O
histórico Git permanece preservado para auditoria; nenhum segredo, corpus,
manuscrito ou banco de dados faz parte do conjunto de remoção.
