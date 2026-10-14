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

const pendingAnalysis = document.querySelector("[data-analysis-id]");

if (pendingAnalysis) {
  const statusMessage = pendingAnalysis.querySelector("[data-poll-status]");
  const readyMessage = statusMessage.textContent;
  let stopped = false;
  let pollTimer;
  let activeRequest;

  async function checkAnalysis() {
    activeRequest = new AbortController();
    const timeout = window.setTimeout(() => activeRequest.abort(), 15000);
    try {
      const response = await fetch(
        `/api/v1/analyses/${encodeURIComponent(pendingAnalysis.dataset.analysisId)}`,
        { cache: "no-store", signal: activeRequest.signal },
      );
      if (response.status === 404) {
        stopped = true;
        pendingAnalysis.setAttribute("aria-busy", "false");
        statusMessage.textContent = "This analysis could not be found. Start a new analysis to try again.";
        return;
      }
      if (!response.ok) throw new Error("Status check failed");
      const analysis = await response.json();
      if (analysis.status === "completed" || analysis.status === "failed") {
        stopped = true;
        window.location.reload();
        return;
      }
      statusMessage.textContent = readyMessage;
    } catch (error) {
      if (!stopped) {
        statusMessage.textContent = "Unable to check progress right now. Reconnecting automatically… You can also refresh this page.";
      }
    } finally {
      window.clearTimeout(timeout);
      if (!stopped) pollTimer = window.setTimeout(checkAnalysis, 2000);
    }
  }

  // Allow polling to resume if the browser restores this page from its back cache.
  window.addEventListener("pagehide", () => {
    stopped = true;
    window.clearTimeout(pollTimer);
    activeRequest?.abort();
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      stopped = false;
      checkAnalysis();
    }
  });
  checkAnalysis();
}
