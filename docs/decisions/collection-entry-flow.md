# Collection Entry Flow

- a tela inicial do sistema exige escolha entre `Book` e `Collection`
- `Book` segue o fluxo padrão existente
- `Collection` abre `web/collections_module/`
- a Collection não reutiliza o formulário de Book
- cadastro, itens, upload, preparação, merge e revisão são próprios da Collection
- a Collection permanece blindada até existir `merged` final
