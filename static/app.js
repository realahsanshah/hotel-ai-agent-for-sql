// Vanilla JS, no build step, no framework — deliberately minimal since the
// point of this project is the agent backend, not the frontend.

const chat = document.getElementById("chat");
const form = document.getElementById("ask-form");
const input = document.getElementById("question-input");
const healthIndicator = document.getElementById("health-indicator");

async function checkHealth() {
  try {
    const res = await fetch("/health");
    const data = await res.json();
    healthIndicator.textContent = data.database ? "database connected" : "database unreachable";
    healthIndicator.className = "pill " + (data.database ? "ok" : "bad");
  } catch {
    healthIndicator.textContent = "backend unreachable";
    healthIndicator.className = "pill bad";
  }
}

function addUserMessage(text) {
  const div = document.createElement("div");
  div.className = "message user";
  div.innerHTML = `<div class="bubble"></div>`;
  div.querySelector(".bubble").textContent = text;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

function addLoadingMessage() {
  const div = document.createElement("div");
  div.className = "message assistant";
  div.innerHTML = `<div class="bubble loading">Thinking…</div>`;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

function renderAssistantMessage(container, data) {
  const sqlBlock = data.sql.length
    ? `<details class="sql-panel">
         <summary>SQL used (${data.sql.length} statement${data.sql.length === 1 ? "" : "s"})</summary>
         <pre></pre>
       </details>`
    : "";

  container.innerHTML = `
    <div class="bubble"></div>
    <div class="meta"></div>
    ${sqlBlock}
  `;
  container.querySelector(".bubble").textContent = data.answer;
  container.querySelector(".meta").textContent =
    `agents: ${data.agents_used.join(" -> ")} · ${data.elapsed_seconds.toFixed(2)}s`;

  const pre = container.querySelector(".sql-panel pre");
  if (pre) pre.textContent = data.sql.join("\n\n");
}

function renderErrorMessage(container, message) {
  container.innerHTML = `<div class="bubble">Error: ${message}</div>`;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = input.value.trim();
  if (!question) return;

  addUserMessage(question);
  input.value = "";
  input.disabled = true;
  form.querySelector("button").disabled = true;

  const assistantDiv = addLoadingMessage();

  try {
    const res = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      renderErrorMessage(assistantDiv, err.detail || "Request failed.");
    } else {
      const data = await res.json();
      renderAssistantMessage(assistantDiv, data);
    }
  } catch (err) {
    renderErrorMessage(assistantDiv, err.message);
  } finally {
    chat.scrollTop = chat.scrollHeight;
    input.disabled = false;
    form.querySelector("button").disabled = false;
    input.focus();
  }
});

checkHealth();
