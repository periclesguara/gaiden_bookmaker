(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  if (!byId("reference-selector")) return;
  const endpoints = byId("reference-endpoints").dataset;
  const dialog = byId("reference-selector");
  const initial = JSON.parse(byId("selected-works-data").textContent);
  let selected = new Map(initial.map(row => [row.work_id, row]));
  let draft = new Map();
  let authors = new Map();
  let author = null;
  let visible = [];
  let nextPage = null;
  let request = null;
  let batchRequest = null;
  let searchTimer;
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const message = text => { byId("corpus-message").textContent = text; };
  const count = () => { byId("draft-count").textContent = draft.size + " obras selecionadas"; };
  function syncForm() {
    const field = byId("id_work_references");
    if (!field) return;
    field.value = JSON.stringify([...selected.values()].map(row => ({
      work_id: row.work_id, reference_role: row.role || "OTHER", notes: row.notes || ""
    })));
    const removed = byId("id_corpus_removed_work_ids");
    if (removed) removed.value = JSON.stringify(initial.filter(row => !selected.has(row.work_id)).map(row => row.work_id));
    const summary = byId("reference-summary");
    summary.replaceChildren();
    const counts = new Map();
    selected.forEach(row => counts.set(row.author, (counts.get(row.author) || 0) + 1));
    counts.forEach((total, name) => summary.append(node("p", name + " · " + total + " obras")));
    summary.append(node("p", "Total: " + selected.size + " obras"));
  }
  function renderWorks() {
    const list = byId("work-list");
    list.replaceChildren();
    visible.forEach(row => {
      const label = node("label", undefined, "reference-work");
      const checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.checked = draft.has(row.work_id);
      checkbox.setAttribute("aria-label", row.title);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) draft.set(row.work_id, {...row, ...selected.get(row.work_id)});
        else draft.delete(row.work_id);
        count();
      });
      const title = node("div");
      title.append(node("strong", row.title), node("small", row.author));
      label.append(checkbox, title);
      list.append(label);
    });
    count();
  }
  async function json(url, params, signal) {
    const response = await fetch(url + "?" + new URLSearchParams(params), {signal});
    if (!response.ok) throw new Error("catalog");
    return response.json();
  }
  function chooseAuthor(row) {
    author = row;
    const select = byId("corpus-author");
    if (![...select.options].some(option => option.value === String(row.id))) select.add(new Option(row.name, row.id));
    select.value = String(row.id);
    byId("corpus-search").value = "";
    byId("author-results").replaceChildren();
    loadWorks();
  }
  async function loadWorks(page = 1) {
    if (request) request.abort();
    request = new AbortController();
    const current = request;
    clearTimeout(searchTimer);
    const query = byId("corpus-search").value.trim();
    const params = {q: query, page};
    if (author) params.author = author.id;
    byId("current-author").textContent = author ? "Obras de " + author.name : "Obras";
    byId("author-actions").hidden = !author;
    byId("more-works").hidden = true;
    if (page === 1) { visible = []; renderWorks(); }
    if (!author && !query) { message("Selecione um autor."); return; }
    message("Carregando…");
    try {
      const data = await json(endpoints.works, params, current.signal);
      if (current.signal.aborted || !dialog.open) return;
      visible.push(...data.results);
      nextPage = data.next_page;
      renderWorks();
      byId("more-works").hidden = !nextPage;
      message(visible.length ? "" : "Nenhuma obra encontrada.");
    } catch (error) {
      if (error.name !== "AbortError") message("Não foi possível carregar obras. Tente novamente.");
    }
  }
  async function loadAuthors(query = "") {
    if (batchRequest) batchRequest.abort();
    batchRequest = new AbortController();
    const current = batchRequest;
    try {
      const data = await json(endpoints.authors, {q: query}, current.signal);
      if (current.signal.aborted || !dialog.open) return;
      const select = byId("corpus-author");
      select.replaceChildren(new Option("Selecione um autor", ""));
      const results = byId("author-results");
      results.replaceChildren();
      authors = new Map(data.results.map(row => [String(row.id), row]));
      data.results.forEach(row => {
        select.add(new Option(row.name + " · " + row.work_count + " obras", row.id));
        if (!query) return;
        const button = node("button", undefined, "author-result");
        button.type = "button";
        button.append(node("span", row.name), node("small", row.work_count + " obras"));
        button.addEventListener("click", () => chooseAuthor(row));
        results.append(button);
      });
      if (author) {
        if (!authors.has(String(author.id))) select.add(new Option(author.name, author.id));
        select.value = String(author.id);
      }
    } catch (error) {
      if (error.name !== "AbortError") message("Não foi possível carregar autores. Tente novamente.");
    }
  }
  async function selectAuthor(clear) {
    if (!author) return;
    const currentAuthor = {...author};
    const controller = new AbortController();
    if (batchRequest) batchRequest.abort();
    batchRequest = controller;
    const buttons = [byId("select-author"), byId("clear-author"), byId("apply-references")];
    buttons.forEach(button => { button.disabled = true; });
    try {
      const data = await json(endpoints.works, {author: currentAuthor.id, all: "1"}, controller.signal);
      if (controller.signal.aborted || !dialog.open) return;
      data.work_ids.forEach(id => {
        if (clear) draft.delete(id);
        else if (!draft.has(id)) draft.set(id, {work_id: id, author_id: currentAuthor.id, author: currentAuthor.name, ...selected.get(id)});
      });
      renderWorks();
    } catch (error) {
      if (error.name !== "AbortError") message("Não foi possível atualizar o autor. Tente novamente.");
    } finally { buttons.forEach(button => { button.disabled = false; }); }
  }
  byId("open-references").addEventListener("click", () => {
    draft = new Map([...selected].map(([id, row]) => [id, {...row}]));
    author = null;
    byId("corpus-search").value = "";
    byId("author-results").replaceChildren();
    dialog.showModal();
    loadAuthors();
    loadWorks();
  });
  byId("corpus-author").addEventListener("change", event => {
    author = authors.get(event.target.value) || null;
    loadWorks();
  });
  byId("corpus-search").addEventListener("input", () => {
    if (request) request.abort();
    if (batchRequest) batchRequest.abort();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      author = null;
      loadAuthors(byId("corpus-search").value.trim());
      loadWorks();
    }, 250);
  });
  byId("more-works").addEventListener("click", () => loadWorks(nextPage));
  byId("select-author").addEventListener("click", () => selectAuthor(false));
  byId("clear-author").addEventListener("click", () => selectAuthor(true));
  byId("cancel-references").addEventListener("click", () => dialog.close());
  byId("apply-references").addEventListener("click", () => {
    if (draft.size > 500) { message("Selecione no máximo 500 obras."); return; }
    if (endpoints.save) {
      const form = byId("corpus-selection-form");
      form.querySelectorAll("[data-selection]").forEach(input => input.remove());
      const add = (name, value) => {
        const input = node("input");
        input.type = "hidden"; input.name = name; input.value = value; input.dataset.selection = "1";
        form.append(input);
      };
      draft.forEach(row => add("work_ids", row.work_id));
      selected.forEach(row => { if (!draft.has(row.work_id)) add("remove_work_ids", row.work_id); });
      byId("apply-references").disabled = true;
      form.requestSubmit();
    } else {
      selected = new Map(draft);
      syncForm();
      dialog.close();
    }
  });
  dialog.addEventListener("close", () => {
    clearTimeout(searchTimer);
    if (request) request.abort();
    if (batchRequest) batchRequest.abort();
  });
  syncForm();
})();
