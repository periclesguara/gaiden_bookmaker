(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const primary = byId("id_language");
  const languageChecks = [...document.querySelectorAll('input[name="project_languages"]')];
  function syncLanguages() {
    languageChecks.forEach(check => {
      if (check.value === primary.value) check.checked = true;
      check.disabled = check.value === primary.value;
    });
    document.querySelectorAll(".language-rule").forEach(section => {
      const code = section.dataset.language;
      section.hidden = !languageChecks.some(check => check.value === code && check.checked);
      section.querySelector(".primary-badge").hidden = code !== primary.value;
      section.querySelector(".set-primary").hidden = code === primary.value;
    });
  }
  primary.addEventListener("change", syncLanguages);
  languageChecks.forEach(check => check.addEventListener("change", syncLanguages));
  document.querySelectorAll("[data-primary]").forEach(button => button.addEventListener("click", () => {
    primary.value = button.dataset.primary;
    syncLanguages();
  }));
  const suggestions = JSON.parse(byId("language-suggestions-data").textContent);
  document.querySelectorAll(".suggestion").forEach(button => button.addEventListener("click", () => {
    const field = byId("id_rules_" + button.dataset.suggestion);
    if (!field.value || window.confirm("Substituir as regras deste idioma pela sugestão?")) field.value = suggestions[button.dataset.suggestion] || "";
  }));
  syncLanguages();
})();
