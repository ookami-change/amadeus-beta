/**
 * Real-device measurement mode (R4). It adds a guided panel on top of the demo
 * and records the demo's own rAF loop; it changes nothing about rendering.
 *
 * Steps: idle 60 s, playback x3, gaze 10 s, three questions, then POST the
 * report to `api/report` next to the page. Field meanings: apps/web/BENCH.md.
 */

export const BENCH_VERSION = 1;
const IDLE_MS = 60_000;
const GAZE_MS = 10_000;
const PLAYS = 3;
const LONG_FRAME_MS = 50;
const MAX_RAW = 9000; // ~75 s at 120 Hz; longer windows are evenly downsampled

export interface BenchHooks {
  /** Play the sample line once from the start; resolves when it ends. */
  play(): Promise<void>;
  /** Current mouth readout, e.g. "音频 ou 1.00". */
  mouth(): string;
  /** Largest mouth-shape weight the model has right now (0–1). */
  mouthWeight(): number;
  renderer(): { gpu: string; pixelRatio: number; bufferWidth: number; bufferHeight: number };
}

interface BenchWindow {
  name: string;
  startedAt: string;
  durationMs: number;
  frames: number;
  valid: boolean;
  hidden: boolean;
  blurred: boolean;
  fpsMedian: number;
  fpsP5: number;
  frameMsMedian: number;
  frameMsP95: number;
  frameMsMax: number;
  longFrames: number;
  downsampled: boolean;
  deltasMs: number[];
  mouth?: { samples: string[]; maxWeight: number };
}

interface Recording {
  name: string;
  started: number;
  startedAt: string;
  deltas: number[];
  last: number;
  hidden: boolean;
  blurred: boolean;
  mouth: string[] | null;
  maxWeight: number;
}

export class Bench {
  private readonly hooks: BenchHooks;
  private readonly panel: HTMLElement;
  private recording: Recording | null = null;
  private readonly windows: BenchWindow[] = [];
  private mouthTimer = 0;

  constructor(hooks: BenchHooks) {
    this.hooks = hooks;
    this.panel = document.createElement("section");
    this.panel.id = "bench";
    document.body.append(this.panel);
    document.body.classList.add("bench");
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && this.recording) this.recording.hidden = true;
    });
    window.addEventListener("blur", () => {
      if (this.recording) this.recording.blurred = true;
    });
    this.intro();
  }

  /** Call once per rAF frame with the frame's timestamp. */
  frame(now: number): void {
    const rec = this.recording;
    if (!rec) return;
    if (rec.last > 0) rec.deltas.push(now - rec.last);
    rec.last = now;
  }

  private intro(): void {
    this.show(
      `<h2>真机测量</h2>
       <p>大约 2 分钟。分四步：待机 60 秒、播放台词 3 次、划动看视线 10 秒、回答三个问题。测完自动提交。</p>
       <p>测量时请保持这一页在前台，别切到别的应用。</p>`,
      [["开始", () => void this.idle()]],
    );
  }

  private async idle(): Promise<void> {
    try {
      await document.documentElement.requestFullscreen?.();
    } catch {
      // Not every browser allows it (iOS Safari); measure in the page as it is.
    }
    await wait(1000);
    this.start("idle");
    await this.countdown(IDLE_MS, "第 1 步：待机", "别碰屏幕，看她 60 秒，留意动作有没有在循环。");
    this.stop();
    this.show(`<h2>第 2 步：播放</h2><p>点下面的按钮，会连着播 ${PLAYS} 次台词，看她的嘴。</p>`, [
      ["播放", () => void this.playback()],
    ]);
  }

  private async playback(): Promise<void> {
    this.show(`<h2>第 2 步：播放</h2><p>正在播放，看她的嘴有没有动。</p>`, []);
    this.start("playback", true);
    for (let i = 0; i < PLAYS; i += 1) {
      await this.hooks.play();
      if (i < PLAYS - 1) await wait(1000);
    }
    this.stop();
    this.show(
      `<h2>第 3 步：视线</h2><p>点「开始」后，用手指或鼠标在画面上慢慢左右、上下划 10 秒，看她的眼睛和头跟不跟。</p>`,
      [["开始", () => void this.gaze()]],
    );
  }

  private async gaze(): Promise<void> {
    this.panel.classList.add("bench-thin");
    this.start("gaze");
    await this.countdown(GAZE_MS, "第 3 步：视线", "在画面上慢慢划动。");
    this.stop();
    this.panel.classList.remove("bench-thin");
    this.questions();
  }

  private questions(): void {
    const q = (name: string, title: string, options: [string, string][]): string =>
      `<fieldset><legend>${title}</legend>${options
        .map(([value, label]) => `<label><input type="radio" name="${name}" value="${value}" /> ${label}</label>`)
        .join("")}</fieldset>`;
    this.show(
      `<h2>第 4 步：三个问题</h2>
       ${q("loop", "60 秒里看得出动作在循环吗", [["none", "看不出"], ["some", "有点"], ["obvious", "明显"]])}
       ${q("gaze", "视线跟着你的手指动吗", [["natural", "自然地跟"], ["stiff", "跟但僵"], ["none", "没动"]])}
       ${q("mouth", "说话时嘴在动吗", [["clear", "清楚"], ["weak", "勉强"], ["none", "没动"]])}
       <label class="bench-text">设备名（可选，比如 REDMI K80、MacBook Air M2）<input name="device" maxlength="80" /></label>
       <label class="bench-text">备注（可选）<textarea name="note" maxlength="1000" rows="2"></textarea></label>
       <p class="bench-error" hidden></p>`,
      [["提交", () => void this.submit()]],
    );
  }

  private async submit(): Promise<void> {
    const value = (name: string): string | null => {
      const node = this.panel.querySelector<HTMLInputElement | HTMLTextAreaElement>(
        `[name="${name}"]:checked, input[name="${name}"]:not([type=radio]), textarea[name="${name}"]`,
      );
      const text = node?.value.trim() ?? "";
      return text === "" ? null : text;
    };
    const answers = { loop: value("loop"), gaze: value("gaze"), mouth: value("mouth") };
    const error = this.panel.querySelector<HTMLElement>(".bench-error");
    if (!answers.loop || !answers.gaze || !answers.mouth) {
      if (error) {
        error.hidden = false;
        error.textContent = "三个问题都要选。";
      }
      return;
    }
    const button = this.panel.querySelector<HTMLButtonElement>("button");
    if (button) button.disabled = true;
    const report = {
      kind: "r4-bench",
      version: BENCH_VERSION,
      page: location.pathname,
      device: await deviceInfo(this.hooks),
      deviceName: value("device"),
      note: value("note"),
      answers,
      windows: this.windows,
    };
    try {
      const response = await fetch("api/report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(report),
      });
      const body = (await response.json()) as { n?: number; error?: string };
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      this.done(body.n ?? 0);
    } catch (err) {
      if (button) button.disabled = false;
      if (error) {
        error.hidden = false;
        error.textContent = `没提交上：${err instanceof Error ? err.message : String(err)}。可以再点一次。`;
      }
    }
  }

  private done(n: number): void {
    const summary = this.windows
      .map((w) => `<li>${label(w.name)}：中位 ${w.fpsMedian} fps，帧耗时中位 ${w.frameMsMedian} ms${w.valid ? "" : "（中途切走过，无效）"}</li>`)
      .join("");
    this.show(
      `<h2>提交好了</h2><p>第 ${n} 份报告。谢谢！</p><ul>${summary}</ul>
       <p>换一台设备再打开同一个链接就能再测一次。</p>`,
      [["再测一次", () => location.reload()]],
    );
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
  }

  private start(name: string, mouth = false): void {
    this.recording = {
      name,
      started: performance.now(),
      startedAt: localTime(),
      deltas: [],
      last: 0,
      hidden: document.hidden,
      blurred: !document.hasFocus(),
      mouth: mouth ? [] : null,
      maxWeight: 0,
    };
    if (mouth) {
      this.mouthTimer = window.setInterval(() => {
        const rec = this.recording;
        if (!rec?.mouth) return;
        rec.mouth.push(this.hooks.mouth());
        rec.maxWeight = Math.max(rec.maxWeight, this.hooks.mouthWeight());
      }, 250);
    }
  }

  private stop(): void {
    const rec = this.recording;
    this.recording = null;
    window.clearInterval(this.mouthTimer);
    if (!rec) return;
    this.windows.push(summarize(rec, performance.now() - rec.started));
  }

  private async countdown(ms: number, title: string, hint: string): Promise<void> {
    const end = performance.now() + ms;
    this.show(`<h2>${title}</h2><p>${hint}</p><p class="bench-count"></p>`, []);
    const count = this.panel.querySelector<HTMLElement>(".bench-count");
    while (performance.now() < end) {
      if (count) count.textContent = `还剩 ${Math.ceil((end - performance.now()) / 1000)} 秒`;
      await wait(250);
    }
  }

  private show(html: string, actions: [string, () => void][]): void {
    this.panel.innerHTML = html;
    for (const [text, run] of actions) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = text;
      button.addEventListener("click", run, { once: text !== "提交" });
      this.panel.append(button);
    }
  }
}

function summarize(rec: Recording, durationMs: number): BenchWindow {
  const deltas = rec.deltas;
  const sorted = [...deltas].sort((a, b) => a - b);
  // FPS per 500 ms bucket, the same way the HUD reads it.
  const buckets: number[] = [];
  let sum = 0;
  let count = 0;
  for (const d of deltas) {
    sum += d;
    count += 1;
    if (sum >= 500) {
      buckets.push((count * 1000) / sum);
      sum = 0;
      count = 0;
    }
  }
  buckets.sort((a, b) => a - b);
  const downsampled = deltas.length > MAX_RAW;
  const raw = downsampled ? deltas.filter((_, i) => i % Math.ceil(deltas.length / MAX_RAW) === 0) : deltas;
  const result: BenchWindow = {
    name: rec.name,
    startedAt: rec.startedAt,
    durationMs: round(durationMs),
    frames: deltas.length,
    valid: !rec.hidden && !rec.blurred && deltas.length > 0,
    hidden: rec.hidden,
    blurred: rec.blurred,
    fpsMedian: round(quantile(buckets, 0.5)),
    fpsP5: round(quantile(buckets, 0.05)),
    frameMsMedian: round(quantile(sorted, 0.5)),
    frameMsP95: round(quantile(sorted, 0.95)),
    frameMsMax: round(sorted.at(-1) ?? 0),
    longFrames: deltas.filter((d) => d > LONG_FRAME_MS).length,
    downsampled,
    deltasMs: raw.map(round),
  };
  if (rec.mouth) result.mouth = { samples: rec.mouth, maxWeight: round(rec.maxWeight * 100) / 100 };
  return result;
}

/** Nearest-rank quantile, matching evals/latency. */
function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const rank = Math.max(1, Math.ceil(q * sorted.length));
  return sorted[rank - 1];
}

async function deviceInfo(hooks: BenchHooks): Promise<Record<string, unknown>> {
  const nav = navigator as Navigator & {
    userAgentData?: { getHighEntropyValues(hints: string[]): Promise<Record<string, unknown>> };
    deviceMemory?: number;
  };
  let uaData: Record<string, unknown> | null = null;
  try {
    uaData = (await nav.userAgentData?.getHighEntropyValues([
      "model",
      "platform",
      "platformVersion",
      "fullVersionList",
      "architecture",
    ])) ?? null;
  } catch {
    uaData = null;
  }
  return {
    userAgent: navigator.userAgent,
    uaData,
    ...hooks.renderer(),
    screen: { width: screen.width, height: screen.height },
    devicePixelRatio: window.devicePixelRatio,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    hardwareConcurrency: navigator.hardwareConcurrency ?? null,
    deviceMemory: nav.deviceMemory ?? null,
    localTime: localTime(),
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
}

function localTime(): string {
  const now = new Date();
  const offset = -now.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const pad = (n: number): string => String(Math.floor(Math.abs(n))).padStart(2, "0");
  const local = new Date(now.getTime() + offset * 60_000).toISOString().slice(0, 19);
  return `${local}${sign}${pad(offset / 60)}:${pad(offset % 60)}`;
}

function label(name: string): string {
  return { idle: "待机", playback: "播放", gaze: "视线" }[name] ?? name;
}

function round(n: number): number {
  return Math.round(n * 10) / 10;
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
