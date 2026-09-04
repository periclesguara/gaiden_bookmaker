# Runtime local quantizado do Writer

O Writer continua a usar endpoints compatíveis com OpenAI. O módulo
`gaiden.runtime` escolhe o backend local somente quando encontra um executável
`llama-server`/`llama` e os dois arquivos GGUF: um gerador e um modelo de
embeddings. Sem todos esses elementos, o modo `auto` mantém o endpoint externo
já configurado; ele não troca silenciosamente o fluxo de produção.

Pesos, índices, logs e rascunhos são artefatos locais. Guarde-os fora do Git e
defina um caminho absoluto, por exemplo:

```bash
export GAIDEN_MODEL_ROOT=/home/operador/gaiden-models
export GAIDEN_RUNTIME_BACKEND=llamacpp
export GAIDEN_RUNTIME_CONTEXT=2048
```

A estrutura esperada é:

```text
$GAIDEN_MODEL_ROOT/
  writer/Qwen3.5-9B-Q4_K_M.gguf
  embedding/Qwen3-Embedding-0.6B-Q8_0.gguf
```

O repositório oficial `Qwen/Qwen3.5-9B` publica pesos no formato Transformers,
não GGUF. Para o backend `llama.cpp`, o operador deve baixar uma conversão GGUF
específica, registrar a revisão e verificar o SHA-256 antes de colocá-la nessa
estrutura. Não use o script de download de pesos Transformers como se ele
produzisse GGUF.

Em GPUs com 6–10 GB de VRAM, a política prioriza `Q4_K_M` para o Writer e deixa
os embeddings na CPU. O `-fit on` do llama.cpp permite descarregar camadas para
RAM se não houver VRAM suficiente. Use contexto de 2048 para a primeira
amostra; aumente somente depois de medir a memória e a latência.

Inspecione o plano sem iniciar servidores:

```bash
python scripts/runtime/gaiden_runtime.py plan
```

Quando o plano indicar `local_ready: true`, inicie os dois endpoints de
loopback com:

```bash
python scripts/runtime/gaiden_runtime.py serve
```

O processo grava logs sob `.runtime/logs/`, caminho já ignorado pelo Git. O
adaptador expõe os serviços somente em `127.0.0.1`; não publique as portas sem
uma revisão de segurança separada.
