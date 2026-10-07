const form = document.querySelector("#analysis-form");
const symbolInput = document.querySelector("#symbol");

if (symbolInput) {
  symbolInput.addEventListener("input", () => {
    symbolInput.value = symbolInput.value.toUpperCase().replace(/\s+/g, "");
  });
}

if (form) {
  form.addEventListener("submit", () => {
    const button = document.querySelector("#analyze-button");
    if (button) {
      button.classList.add("is-loading");
      button.setAttribute("aria-busy", "true");
      button.disabled = true;
    }
  });
}

