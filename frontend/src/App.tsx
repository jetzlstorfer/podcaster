import React, { useCallback, useEffect, useMemo, useState } from "react";

import {
  backendUrl,
  deleteEpisode,
  getEpisode,
  listEpisodes,
  runPodcast,
} from "./api";
import {
  EpisodeSummary,
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
  const [isDownloading, setIsDownloading] = useState(false);
  const [episodes, setEpisodes] = useState<EpisodeSummary[]>([]);
  const [episodesLoading, setEpisodesLoading] = useState(false);
  const [selectedEpisodeId, setSelectedEpisodeId] = useState<string | null>(null);
  const [deletingEpisodeId, setDeletingEpisodeId] = useState<string | null>(null);

  const isRunning = status === "running";
  const activeStages = useMemo(
    () => new Set([...startedStages].filter((stage) => !doneStages.has(stage))),
    [startedStages, doneStages],
  );
  const visibleActiveStages = useMemo(() => {
    if (!isRunning || startedStages.size > 0) return activeStages;
    return new Set<Stage>(["parse"]);
  }, [activeStages, isRunning, startedStages.size]);

  const loadEpisodes = useCallback(async () => {
    setEpisodesLoading(true);
    try {
      const data = await listEpisodes();
      setEpisodes(data);
    } catch {
      // History loading should not block generation UI.
    } finally {
      setEpisodesLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadEpisodes();
  }, [loadEpisodes]);

  const handleOpenEpisode = async (episodeId: string) => {
    if (isRunning) return;
    setSelectedEpisodeId(episodeId);
    setError(null);
    try {
      const episode = await getEpisode(episodeId);
      setResult(episode);
      setStatus("done");
      setStartedStages(new Set());
      setDoneStages(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleDeleteEpisode = async (episode: EpisodeSummary) => {
    if (
      isRunning ||
      deletingEpisodeId ||
      !window.confirm(`Delete "${episode.title}" and all of its files?`)
    ) {
      return;
    }

    setDeletingEpisodeId(episode.id);
    setError(null);
    try {
      await deleteEpisode(episode.id);
      setEpisodes((current) => current.filter((item) => item.id !== episode.id));
      if (selectedEpisodeId === episode.id) {
        setSelectedEpisodeId(null);
        setResult(null);
        setStatus("idle");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeletingEpisodeId(null);
    }
  };

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

    await loadEpisodes();

    // If the stream ended without an explicit result or error, settle status.
    setStatus((prev) => (prev === "running" ? "done" : prev));
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void handleGenerate();
  };

  const audioIsUrl = result?.audio?.startsWith("/audio/");
  const imageIsUrl = result?.image?.startsWith("/images/");

  const handleDownload = async () => {
    if (!result || !audioIsUrl || isDownloading) return;

    setIsDownloading(true);
    try {
      const response = await fetch(backendUrl + result.audio);
      if (!response.ok) {
        throw new Error(`Download failed (${response.status})`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${result.title
        .replace(/[^\w\s-]/g, "")
        .trim()
        .replace(/\s+/g, "_") || "podcast"}.mp3`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <div className="layout">
      <aside className="sidebar card">
        <div className="sidebar-header">
          <h2>Episode Library</h2>
          <button className="sidebar-refresh" onClick={() => void loadEpisodes()}>
            Refresh
          </button>
        </div>
        {episodesLoading && <p className="sidebar-note">Loading episodes…</p>}
        {!episodesLoading && episodes.length === 0 && (
          <p className="sidebar-note">No generated episodes yet.</p>
        )}
        <ul className="episode-list">
          {episodes.map((episode) => (
            <li className="episode-row" key={episode.id}>
              <button
                className={`episode-item ${selectedEpisodeId === episode.id ? "episode-item-active" : ""}`}
                onClick={() => void handleOpenEpisode(episode.id)}
                disabled={isRunning || deletingEpisodeId === episode.id}
              >
                <span className="episode-title">{episode.title}</span>
                <span className="episode-meta">
                  {episode.turns} turns · {episode.language}
                </span>
              </button>
              <button
                className="episode-delete"
                type="button"
                aria-label={`Delete ${episode.title}`}
                title="Delete episode"
                disabled={isRunning || deletingEpisodeId !== null}
                onClick={() => void handleDeleteEpisode(episode)}
              >
                {deletingEpisodeId === episode.id ? "…" : "×"}
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <main className="page">
        <header className="hero">
        <p className="eyebrow">Multi-agent</p>
        <div className="h1-wrapper">
          <span className="emoji">🎙️</span>
          <h1>Podcaster</h1>
        </div>
        <p className="subtitle">
          Turn any topic into a two-host podcast.<br />Pick a length and language,
          then generate.
        </p>
        </header>

        <form className="card" onSubmit={handleSubmit}>
        <label className="field">
          <span>Topic</span>
          <textarea
            value={topic}
            placeholder="e.g. The latest breakthroughs in quantum computing"
            rows={3}
            disabled={isRunning}
            onChange={(e) => setTopic(e.target.value)}
            onKeyDown={(e) => {
              if (e.ctrlKey && e.key === "Enter") {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
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
          type="submit"
          disabled={isRunning || !topic.trim()}
        >
          {isRunning ? "Generating…" : "Generate podcast"}
        </button>
        </form>

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
            <>
              <audio className="player" controls src={backendUrl + result.audio} />
              <div className="result-actions">
                <button
                  className="download"
                  onClick={handleDownload}
                  disabled={isDownloading}
                >
                  {isDownloading ? "Downloading…" : "Download episode (.mp3)"}
                </button>
              </div>
            </>
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

        <footer className="site-footer">
          <p>
            made with ❤️ and 🤖<br />hosted on{" "}
            <a
              href="https://github.com/jetzlstorfer/podcaster"
              target="_blank"
              rel="noreferrer"
            >
              github
            </a>
          </p>
        </footer>
      </main>
    </div>
  );
}
