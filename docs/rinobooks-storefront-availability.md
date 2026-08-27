# RinoBooks — disponibilidade comercial automática

## Objetivo

Separar o estado editorial do estado comercial da vitrine.

O envio Gaiden -> RinoBooks continua fail-closed e sempre chega como `DRAFT`.
Esse `DRAFT` não publica página, não entra no sitemap e não dispara indexação.

Depois que a edição existe na RinoBooks, a vitrine deve derivar automaticamente
a disponibilidade comercial a partir de dois sinais objetivos:

1. existe EPUB anexado/validado;
2. existe ao menos um canal de venda HTTPS marcado como ativo.

## Estados derivados

| EPUB | Canal ativo | availability.status | Texto da página |
|---|---|---|---|
| não | qualquer | `NOT_ATTACHED` | E-book não anexado |
| sim | não | `COMING_SOON` | Lançamento em breve |
| sim | sim | `LAUNCHED` | Lançado |

`availability.status` é derivado. Não deve ser editado manualmente nem usado
como substituto para o status editorial/publicação do receptor.

## Canais de venda

Os campos legados continuam válidos:

- `lulu_url` -> canal `Lulu`;
- `hotmart_url` -> canal `Hotmart`.

Para outras lojas, `EditionMetadata.sales_channels` aceita uma lista JSON:

```json
[
  {
    "name": "IngramSpark",
    "url": "https://shop.example.com/book/123",
    "active": true
  },
  {
    "name": "Outra loja",
    "url": "https://store.example.com/title/456",
    "active": false
  }
]
```

Regras:

- somente URLs `https://` com host válido são consideradas;
- `active: false` nunca conta como canal disponível;
- Lulu/Hotmart e canais genéricos são deduplicados por URL;
- uma URL cadastrada mas inválida não altera a página para `LAUNCHED`;
- o catálogo pode exibir todos os canais ativos como botões de compra.

## Contrato enviado à RinoBooks

O `storefront` do manifesto v2 passa a conter:

```json
{
  "sales_channels": [
    {
      "name": "Lulu",
      "url": "https://www.lulu.com/shop/...",
      "active": true
    }
  ],
  "availability": {
    "status": "LAUNCHED",
    "label": "Lançado",
    "ebook_attached": true,
    "active_sales_channels": [
      {
        "name": "Lulu",
        "url": "https://www.lulu.com/shop/...",
        "active": true
      }
    ]
  }
}
```

Se o EPUB estiver anexado mas nenhum canal estiver ativo:

```json
{
  "availability": {
    "status": "COMING_SOON",
    "label": "Lançamento em breve",
    "ebook_attached": true,
    "active_sales_channels": []
  }
}
```

## Regra para o receptor/site

Na página pública e no card do catálogo, usar `storefront.availability` para o
badge e a área comercial:

- `COMING_SOON`: mostrar "Lançamento em breve" e permitir aviso de lançamento;
- `LAUNCHED`: mostrar "Lançado", remover o aviso de lançamento e renderizar os
  botões para `active_sales_channels`;
- `NOT_ATTACHED`: não expor compra nem marcar como lançamento.

A presença de um canal comercial não pode, sozinha, transformar um registro
editorial `DRAFT` em página pública. A publicação editorial continua sendo uma
ação/control gate da RinoBooks. A automação desta regra atua apenas sobre a
disponibilidade comercial de uma página já autorizada a existir publicamente.
