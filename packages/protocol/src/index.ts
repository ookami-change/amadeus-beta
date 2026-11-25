/**
 * Conversation protocol shared by the web client and the agent.
 *
 * `spec.json` is the definition. These types and `services/agent/src/agent/protocol.py`
 * are bindings of it; both test suites check themselves against the spec and the
 * shared `fixtures.json`. Do not add message types without the architect.
 */
import spec from "../spec.json" with { type: "json" };

export const PROTOCOL_VERSION = "0.1.0" as const;
export const MIC_TAG = 1;
export const SPEECH_TAG = 2;
export const MIC_SAMPLE_RATE = 16000;

export type DirectorState = "idle" | "listening" | "thinking" | "speaking" | "interrupted";

export interface LipsyncMark {
  start_ms: number;
  end_ms: number;
  text: string;
}

export interface Hello {
  type: "hello";
  protocol: string;
  client: string;
  sample_rate: number;
}
export interface Ping {
  type: "ping";
  id: number;
  client_ms: number;
}
export interface Playback {
  type: "playback";
  utterance: number;
  event: "started" | "stopped" | "finished";
  played_ms: number;
  client_ms: number;
}
export interface Bye {
  type: "bye";
}

export interface Welcome {
  type: "welcome";
  protocol: string;
  session: string;
  speech_rate: number;
}
export interface Pong {
  type: "pong";
  id: number;
  client_ms: number;
  server_ms: number;
}
export interface Transcript {
  type: "transcript";
  turn: number;
  text: string;
  final: boolean;
}
export interface State {
  type: "state";
  state: DirectorState;
  turn: number | null;
}
export interface Prepare {
  type: "prepare";
  turn: number;
}
export interface SpeakStart {
  type: "speak_start";
  utterance: number;
  turn: number;
}
export interface SpeakText {
  type: "speak_text";
  utterance: number;
  text: string;
}
export interface Lipsync {
  type: "lipsync";
  utterance: number;
  marks: LipsyncMark[];
}
export interface SpeakEnd {
  type: "speak_end";
  utterance: number;
  reason: "done" | "interrupted" | "error";
}
export interface StopAudio {
  type: "stop_audio";
  utterance: number;
}
export interface TurnReport {
  type: "turn_report";
  turn: number;
  onset_ms: number;
  commit_ms: number | null;
  commit_text: string;
  final_text: string;
  launches: number;
  cancelled: number;
  restarts: number;
  launch_ms: number | null;
  first_token_ms: number | null;
  ready_ms: number | null;
  speak_ms: number | null;
  /** Synthesizer sockets opened during the turn (MiniMax counts these against its RPM). */
  sockets: number;
  /** How long the kept reply waited for a synthesizer socket after its first clause. */
  voice_wait_ms: number | null;
  error: string | null;
}
export interface ErrorMessage {
  type: "error";
  code: string;
  message: string;
  retryable: boolean;
}

export type ClientMessage = Hello | Ping | Playback | Bye;
export type ServerMessage =
  | Welcome
  | Pong
  | Transcript
  | State
  | Prepare
  | SpeakStart
  | SpeakText
  | Lipsync
  | SpeakEnd
  | StopAudio
  | TurnReport
  | ErrorMessage;
export type Message = ClientMessage | ServerMessage;

type FieldSpec = string;
interface MessageSpec {
  from: string;
  fields: Record<string, FieldSpec>;
}
const MESSAGES = spec.messages as Record<string, MessageSpec>;
const STRUCTS = spec.structs as Record<string, Record<string, FieldSpec>>;

/** Throws when `value` does not match the field type written in spec.json. */
function check(kind: FieldSpec, value: unknown, path: string): void {
  if (kind.endsWith("?")) {
    if (value === null) return;
    check(kind.slice(0, -1), value, path);
    return;
  }
  if (kind.endsWith("[]")) {
    if (!Array.isArray(value)) throw new Error(`${path}: expected an array`);
    value.forEach((one, index) => check(kind.slice(0, -2), one, `${path}[${index}]`));
    return;
  }
  if (kind.startsWith("enum:")) {
    if (typeof value !== "string" || !kind.slice(5).split("|").includes(value)) {
      throw new Error(`${path}: expected one of ${kind.slice(5)}`);
    }
    return;
  }
  const struct = STRUCTS[kind];
  if (struct !== undefined) {
    checkFields(struct, value, path);
    return;
  }
  const ok =
    (kind === "string" && typeof value === "string") ||
    (kind === "bool" && typeof value === "boolean") ||
    (kind === "number" && typeof value === "number" && Number.isFinite(value)) ||
    (kind === "int" && typeof value === "number" && Number.isInteger(value));
  if (!ok) throw new Error(`${path}: expected ${kind}`);
}

function checkFields(fields: Record<string, FieldSpec>, value: unknown, path: string): void {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${path}: expected an object`);
  }
  const record = value as Record<string, unknown>;
  for (const [name, kind] of Object.entries(fields)) {
    if (!(name in record)) throw new Error(`${path}.${name}: missing`);
    check(kind, record[name], `${path}.${name}`);
  }
}

/** Parse one text frame. Throws on anything spec.json does not allow. */
export function parseMessage(text: string): Message {
  const value: unknown = JSON.parse(text);
  if (typeof value !== "object" || value === null) throw new Error("message: expected an object");
  const type = (value as { type?: unknown }).type;
  if (typeof type !== "string" || MESSAGES[type] === undefined) {
    throw new Error(`message: unknown type ${String(type)}`);
  }
  checkFields(MESSAGES[type].fields, value, type);
  return value as Message;
}

export function encodeMessage(message: Message): string {
  return JSON.stringify(message);
}

/** A mic frame: tag byte, then 16 kHz s16le PCM. */
export function encodeMic(pcm: Int16Array): ArrayBuffer {
  const out = new ArrayBuffer(1 + pcm.byteLength);
  const view = new DataView(out);
  view.setUint8(0, MIC_TAG);
  for (let i = 0; i < pcm.length; i += 1) view.setInt16(1 + i * 2, pcm[i] ?? 0, true);
  return out;
}

export interface SpeechFrame {
  utterance: number;
  pcm: Int16Array;
}

/** A speech frame from the server: tag byte, utterance u32 LE, then s16le PCM. */
export function decodeSpeech(data: ArrayBuffer): SpeechFrame {
  const view = new DataView(data);
  if (data.byteLength < 5 || view.getUint8(0) !== SPEECH_TAG) throw new Error("not a speech frame");
  const utterance = view.getUint32(1, true);
  const samples = Math.floor((data.byteLength - 5) / 2);
  const pcm = new Int16Array(samples);
  for (let i = 0; i < samples; i += 1) pcm[i] = view.getInt16(5 + i * 2, true);
  return { utterance, pcm };
}
