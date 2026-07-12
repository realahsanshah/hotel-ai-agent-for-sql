"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function Home() {
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [dbHealthy, setDbHealthy] = useState(null); // null = checking

  // One id per page load, sent with every /ask call so the backend can look
  // up this conversation's prior turns (see app/analyst.py). It's not
  // persisted anywhere (no localStorage) — refreshing the page starts a new
  // conversation, which is fine for a single-user learning project.
  const [sessionId] = useState(() => crypto.randomUUID());

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((res) => res.json())
      .then((data) => setDbHealthy(Boolean(data.database)))
      .catch(() => setDbHealthy(false));
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || loading) return;

    setMessages((prev) => [...prev, { role: "user", text: trimmed }]);
    setQuestion("");
    setLoading(true);

    try {
      const res = await fetch(`${API_URL}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed, session_id: sessionId }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        setMessages((prev) => [
          ...prev,
          { role: "error", text: err.detail || "Request failed." },
        ]);
      } else {
        const data = await res.json();
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: data.answer,
            sql: data.sql,
            agentsUsed: data.agents_used,
            elapsedSeconds: data.elapsed_seconds,
          },
        ]);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "error", text: err.message || "Could not reach the backend." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-screen bg-neutral-950 text-neutral-100">
      <header className="px-6 py-4 border-b border-neutral-800 flex items-center gap-3">
        <h1 className="text-lg font-semibold">Hotel Data Analyst</h1>
        <span
          className={`text-xs px-2.5 py-0.5 rounded-full border ${
            dbHealthy === null
              ? "border-neutral-700 text-neutral-400"
              : dbHealthy
                ? "border-green-800 text-green-400"
                : "border-red-800 text-red-400"
          }`}
        >
          {dbHealthy === null
            ? "checking..."
            : dbHealthy
              ? "database connected"
              : "database unreachable"}
        </span>
      </header>

      <main className="flex-1 flex flex-col max-w-3xl w-full mx-auto px-6 py-4 overflow-hidden">
        <div className="flex-1 overflow-y-auto flex flex-col gap-4 pb-4">
          {messages.map((m, i) => (
            <MessageBubble key={i} message={m} />
          ))}
          {loading && (
            <div className="self-start max-w-[80%]">
              <div className="rounded-xl border border-neutral-800 bg-neutral-900 px-4 py-2 italic text-neutral-400">
                Thinking…
              </div>
            </div>
          )}
        </div>

        <form onSubmit={handleSubmit} className="flex gap-2 pt-3 border-t border-neutral-800">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="e.g. Which reservation statuses exist, and how many of each?"
            autoComplete="off"
            className="flex-1 rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="submit"
            disabled={loading}
            className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-medium disabled:opacity-50"
          >
            Ask
          </button>
        </form>
      </main>
    </div>
  );
}

function MessageBubble({ message }) {
  if (message.role === "user") {
    return (
      <div className="self-end max-w-[80%]">
        <div className="rounded-xl bg-blue-900/60 px-4 py-2">{message.text}</div>
      </div>
    );
  }

  if (message.role === "error") {
    return (
      <div className="self-start max-w-[80%]">
        <div className="rounded-xl border border-red-900 bg-red-950 px-4 py-2 text-red-300">
          Error: {message.text}
        </div>
      </div>
    );
  }

  return (
    <div className="self-start max-w-[80%]">
      <div className="rounded-xl border border-neutral-800 bg-neutral-900 px-4 py-2 whitespace-pre-wrap">
        {message.text}
      </div>
      <div className="mt-1 text-xs text-neutral-500">
        agents: {message.agentsUsed?.join(" -> ")} · {message.elapsedSeconds?.toFixed(2)}s
      </div>
      {message.sql?.length > 0 && (
        <details className="mt-2 rounded-lg border border-neutral-800 bg-neutral-950 px-3 py-2 text-xs">
          <summary className="cursor-pointer text-neutral-400">
            SQL used ({message.sql.length} statement{message.sql.length === 1 ? "" : "s"})
          </summary>
          <pre className="mt-2 whitespace-pre-wrap break-words text-blue-200">
            {message.sql.join("\n\n")}
          </pre>
        </details>
      )}
    </div>
  );
}
