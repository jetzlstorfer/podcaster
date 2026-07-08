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

export const STAGE_LABELS: Record<Stage, string> = {
  parse: "Reading request",
  research: "Researching",
  write_script: "Writing script",
  narrate: "Narrating audio",
  generate_image: "Generating cover art",
  finalize: "Finishing up",
};
