import React, { useMemo, useState } from "react";

import { backendUrl, runPodcast } from "./api";
import {
  Language,
  Length,
  PodcastResult,
  STAGE_GROUPS,
  STAGE_LABELS,
  Stage,
} from "./types";

type Status = "idle" | "running" | "done" | "error";

function getStageState(
  stage: Stage,
  status: Status,
  doneStages: ReadonlySet<Stage>,
  activeStages: ReadonlySet<Stage>,
): "done" | "active" | "pending" {
  if (status === "done" || doneStages.has(stage)) return "done";
  if (activeStages.has(stage)) return "active";
  return "pending";
}

/** Render a turn's text, styling inline performance cues like "[laughs]". */
function renderTurnText(text: string): React.JSX.Element[] {
  return text.split(/(\[[^\]]+\])/g).map((part, i) =>
    /^\[[^\]]+\]$/.test(part) ? (
      <em key={i} className="cue">
        {part}
      </em>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

export default function App(): React.JSX.Element {
  const [topic, setTopic] = useState("");
  const [length, setLength] = useState<Length>("medium");
  const [language, setLanguage] = useState<Language>("english");

  const [status, setStatus] = useState<Status>("idle");
  const [startedStages, setStartedStages] = useState<Set<Stage>>(new Set());
  const [doneStages, setDoneStages] = useState<Set<Stage>>(new Set());
  const [result, setResult] = useState<PodcastResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isRunning = status === "running";
  const activeStages = useMemo(
    () => new Set([...startedStages].filter((stage) => !doneStages.has(stage))),
    [startedStages, doneStages],
  );
  const visibleActiveStages = useMemo(() => {
    if (!isRunning || startedStages.size > 0) return activeStages;
    return new Set<Stage>(["parse"]);
  }, [activeStages, isRunning, startedStages.size]);

  const handleGenerate = async () => {
    if (!topic.trim() || isRunning) return;
    setStatus("running");
    setStartedStages(new Set());
    setDoneStages(new Set());
    setResult(null);
    setError(null);

    await runPodcast(
      { topic: topic.trim(), length, language },
      {
        onStageStarted: (stage) =>
          setStartedStages((prev) => new Set([...prev, stage])),
        onStageFinished: (stage) =>
          setDoneStages((prev) => new Set([...prev, stage])),
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
  const imageIsUrl = result?.image?.startsWith("/images/");

  return (
    <div className="page">
      <header className="hero">
        <p className="eyebrow">Multi-agent</p>
        <h1>🎙️ Podcaster</h1>
        <p className="subtitle">
          Turn any topic into a two-host podcast.<br />Pick a length and language,
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
            {STAGE_GROUPS.map((group) =>
              group.length === 1 ? (
                <li
                  key={group[0]}
                  className={`step step-${getStageState(group[0], status, doneStages, visibleActiveStages)}`}
                >
                  <span className="dot" />
                  {STAGE_LABELS[group[0]]}
                </li>
              ) : (
                <li key={group.join("-")} className="step-group">
                  <span className="group-label">In parallel</span>
                  <ul className="parallel-steps">
                    {group.map((stage) => {
                      const state = getStageState(
                        stage,
                        status,
                        doneStages,
                        visibleActiveStages,
                      );
                      return (
                        <li key={stage} className={`step step-${state}`}>
                          <span className="dot" />
                          {STAGE_LABELS[stage]}
                        </li>
                      );
                    })}
                  </ul>
                </li>
              ),
            )}
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

          {imageIsUrl && (
            <img
              className="cover"
              src={backendUrl + result.image}
              alt={`Cover art for ${result.title}`}
            />
          )}

          {audioIsUrl ? (
            <audio className="player" controls src={backendUrl + result.audio} />
          ) : (
            <p className="note">{result.audio}</p>
          )}

          <div className="script">
            {result.script.map((turn, i) => (
              <p key={i} className={`turn turn-${turn.speaker.toLowerCase()}`}>
                <strong>{turn.speaker}</strong>
                {turn.style && turn.style !== "neutral" && (
                  <em className="style-tag">{turn.style}</em>
                )}
                <span>{renderTurnText(turn.text)}</span>
              </p>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
