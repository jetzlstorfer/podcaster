export type Length = "short" | "medium" | "long";
export type Language = "english" | "german";

export interface PodcastRequest {
  topic: string;
  length: Length;
  language: Language;
}

export interface ScriptTurn {
  speaker: string;
  text: string;
  style?: string;
}

export interface PodcastResult {
  title: string;
  turns: number;
  language: Language;
  audio: string;
  image?: string;
  script: ScriptTurn[];
}

export interface EpisodeSummary {
  id: string;
  title: string;
  language: Language;
  turns: number;
  audio: string;
  image?: string;
  updated: number;
}

/** Pipeline stages, in order, matching the backend executor ids. */
export const STAGES = [
  "parse",
  "research",
  "write_script",
  "narrate",
  "generate_image",
  "finalize",
] as const;
export type Stage = (typeof STAGES)[number];
export const PARALLEL_STAGES: readonly Stage[] = ["narrate", "generate_image"];
export const STAGE_GROUPS: ReadonlyArray<ReadonlyArray<Stage>> = [
  ["parse"],
  ["research"],
  ["write_script"],
  PARALLEL_STAGES,
  ["finalize"],
];

export const STAGE_LABELS: Record<Stage, string> = {
  parse: "Reading user request",
  research: "Researching on the topic",
  write_script: "Writing podcast script",
  narrate: "Narrating audio",
  generate_image: "Generating cover art",
  finalize: "Finishing up",
};
