import { HttpAgent } from "@ag-ui/client";

import { PodcastRequest, PodcastResult, STAGES, Stage } from "./types";

const BACKEND_URL: string =
  (import.meta.env.VITE_BACKEND_URL as string | undefined) ??
  "http://127.0.0.1:8089";

export const backendUrl = BACKEND_URL.replace(/\/$/, "");

function randomId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/** Recursively search an event payload for the workflow's final result dict. */
function findResult(value: unknown): PodcastResult | null {
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findResult(item);
      if (found) return found;
    }
    return null;
  }
  if (value && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    if (typeof obj.title === "string" && Array.isArray(obj.script)) {
      return obj as unknown as PodcastResult;
    }
    // The dict may arrive as a JSON string on some event fields.
    for (const nested of Object.values(obj)) {
      const found = findResult(nested);
      if (found) return found;
    }
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      try {
        return findResult(JSON.parse(trimmed));
      } catch {
        return null;
      }
    }
  }
  return null;
}

export interface RunCallbacks {
  onStage?: (stage: Stage) => void;
  onResult?: (result: PodcastResult) => void;
  onError?: (error: Error) => void;
}

/** Run the podcast pipeline over AG-UI and stream progress back to the UI. */
export async function runPodcast(
  request: PodcastRequest,
  callbacks: RunCallbacks,
): Promise<void> {
  const agent = new HttpAgent({ url: `${backendUrl}/podcast` });
  agent.messages = [
    {
      id: randomId(),
      role: "user",
      content: JSON.stringify(request),
    },
  ];

  let resolved: PodcastResult | null = null;

  const capture = (event: unknown) => {
    if (resolved) return;
    const found = findResult(event);
    if (found) {
      resolved = found;
      callbacks.onResult?.(found);
    }
  };

  try {
    await agent.runAgent(
      {},
      {
        onStepStartedEvent: ({ event }) => {
          // The workflow emits STEP_STARTED for inner steps too (e.g. the
          // scriptwriter agent), whose names aren't pipeline stages. Ignore
          // those so an unknown name never blanks the progress indicator.
          const name = (event as { stepName?: string }).stepName;
          if (name && (STAGES as readonly string[]).includes(name)) {
            callbacks.onStage?.(name as Stage);
          }
        },
        onCustomEvent: ({ event }) => capture(event),
        onRunFinishedEvent: ({ event, result }) => {
          capture(result);
          capture(event);
        },
        onRunErrorEvent: ({ event }) => {
          const message =
            (event as { message?: string }).message ?? "Run failed";
          callbacks.onError?.(new Error(message));
        },
        onEvent: ({ event }) => capture(event),
      },
    );
  } catch (err) {
    callbacks.onError?.(err instanceof Error ? err : new Error(String(err)));
  }
}
