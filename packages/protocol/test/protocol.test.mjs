// Runs with `node --test`; Node strips the types from ../src/index.ts.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  PROTOCOL_VERSION,
  decodeSpeech,
  encodeMessage,
  encodeMic,
  parseMessage,
} from "../src/index.ts";

const spec = JSON.parse(readFileSync(new URL("../spec.json", import.meta.url), "utf8"));
const fixtures = JSON.parse(readFileSync(new URL("../fixtures.json", import.meta.url), "utf8"));

test("version matches the spec", () => {
  assert.equal(PROTOCOL_VERSION, spec.version);
});

test("every fixture parses and round-trips", () => {
  for (const one of fixtures) {
    const parsed = parseMessage(JSON.stringify(one));
    assert.deepEqual(JSON.parse(encodeMessage(parsed)), one);
  }
});

test("fixtures cover every message type", () => {
  const seen = new Set(fixtures.map((one) => one.type));
  assert.deepEqual([...seen].sort(), Object.keys(spec.messages).sort());
});

test("bad messages are rejected", () => {
  assert.throws(() => parseMessage('{"type":"nope"}'));
  assert.throws(() => parseMessage('{"type":"ping","id":1.5,"client_ms":0}'));
  assert.throws(() => parseMessage('{"type":"state","state":"asleep","turn":null}'));
  assert.throws(() => parseMessage('{"type":"transcript","turn":1,"text":"x"}'));
  assert.throws(() => parseMessage('{"type":"lipsync","utterance":1,"marks":[{"start_ms":0}]}'));
});

test("binary frames", () => {
  const mic = new Uint8Array(encodeMic(new Int16Array([1, -2, 300])));
  assert.deepEqual([...mic], [spec.binary.mic.tag, 1, 0, 254, 255, 44, 1]);
  const speech = new Uint8Array([spec.binary.speech.tag, 7, 0, 0, 0, 1, 0, 254, 255]);
  const frame = decodeSpeech(speech.buffer);
  assert.equal(frame.utterance, 7);
  assert.deepEqual([...frame.pcm], [1, -2]);
});
