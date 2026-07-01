import { useState } from "react";

import { backendUrl, runPodcast } from "./api";
import {
  Language,
  Length,
  PodcastResult,
  STAGES,
  STAGE_LABELS,
  Stage,
} from "./types";

type Status = "idle" | "running" | "done" | "error";

export default function App(): JSX.Element {
  const [topic, setTopic] = useState("");
  const [length, setLength] = useState<Length>("medium");
  const [language, setLanguage] = useState<Language>("english");

  const [status, setStatus] = useState<Status>("idle");
  const [activeStage, setActiveStage] = useState<Stage | null>(null);
  const [result, setResult] = useState<PodcastResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isRunning = status === "running";
  const activeIndex = activeStage ? STAGES.indexOf(activeStage) : -1;

  const handleGenerate = async () => {
    if (!topic.trim() || isRunning) return;
    setStatus("running");
    setActiveStage(null);
    setResult(null);
    setError(null);

    await runPodcast(
      { topic: topic.trim(), length, language },
      {
        onStage: (stage) => setActiveStage(stage),
        onResult: (r) => {
          setResult(r);
          setStatus("done");
        },
        onError: (err) => {
          setError(err.message);
          setStatus("error");
        },
      },
    );

    // If the stream ended without an explicit result or error, settle status.
    setStatus((prev) => (prev === "running" ? "done" : prev));
  };

  const audioIsUrl = result?.audio?.startsWith("/audio/");

  return (
    <div className="page">
      <header className="hero">
        <p className="eyebrow">AG-UI · Multi-agent</p>
        <h1>Podcaster</h1>
        <p className="subtitle">
          Turn any topic into a two-host podcast. Pick a length and language,
          then generate.
        </p>
      </header>

      <section className="card">
        <label className="field">
          <span>Topic</span>
          <textarea
            value={topic}
            placeholder="e.g. The latest breakthroughs in quantum computing"
            rows={3}
            disabled={isRunning}
            onChange={(e) => setTopic(e.target.value)}
          />
        </label>

        <div className="row">
          <label className="field">
            <span>Length</span>
            <select
              value={length}
              disabled={isRunning}
              onChange={(e) => setLength(e.target.value as Length)}
            >
              <option value="short">Short (~5 min)</option>
              <option value="medium">Medium (~10 min)</option>
              <option value="long">Long (~30 min) 💎 Premium Feature</option>
            </select>
          </label>

          <label className="field">
            <span>Language</span>
            <select
              value={language}
              disabled={isRunning}
              onChange={(e) => setLanguage(e.target.value as Language)}
            >
              <option value="english">English</option>
              <option value="german">German (work in progress) 💎 Premium Feature</option>
            </select>
          </label>
        </div>

        <button
          className="generate"
          onClick={handleGenerate}
          disabled={isRunning || !topic.trim()}
        >
          {isRunning ? "Generating…" : "Generate podcast"}
        </button>
      </section>

      {status !== "idle" && (
        <section className="card">
          <h2>Progress</h2>
          <ol className="steps">
            {STAGES.map((stage, i) => {
              const state =
                status === "done"
                  ? "done"
                  : i < activeIndex
                    ? "done"
                    : i === activeIndex
                      ? "active"
                      : "pending";
              return (
                <li key={stage} className={`step step-${state}`}>
                  <span className="dot" />
                  {STAGE_LABELS[stage]}
                </li>
              );
            })}
          </ol>
          {error && <p className="error">Error: {error}</p>}
        </section>
      )}

      {result && (
        <section className="card">
          <h2>{result.title}</h2>
          <p className="meta">
            {result.turns} turns · {result.language}
          </p>

          {audioIsUrl ? (
            <audio className="player" controls src={backendUrl + result.audio} />
          ) : (
            <p className="note">{result.audio}</p>
          )}

          <div className="script">
            {result.script.map((turn, i) => (
              <p key={i} className={`turn turn-${turn.speaker.toLowerCase()}`}>
                <strong>{turn.speaker}</strong>
                <span>{turn.text}</span>
              </p>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
