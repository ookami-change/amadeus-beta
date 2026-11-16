import type { VRM } from "@pixiv/three-vrm";

const VOWELS = ["aa", "ih", "ou", "ee", "oh"] as const;
type Vowel = (typeof VOWELS)[number];

export interface VisemeKey {
  t: number;
  v: Vowel;
  w: number;
}

/**
 * Audio-driven 5-vowel estimate, plus a timestamp player.
 * Mouth shapes follow the audio element's clock, not Date.now().
 */
export class LipSync {
  private readonly weights: Record<Vowel, number> = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
  private readonly time = new Uint8Array(1024);
  private readonly freq = new Uint8Array(512);
  private timeline: VisemeKey[] = [];
  mode: "audio" | "timestamp" = "audio";
  readout = "—";

  constructor(
    private readonly vrm: VRM,
    private readonly analyser: AnalyserNode,
    private readonly audio: HTMLAudioElement,
  ) {
    this.analyser.fftSize = 1024;
  }

  setTimeline(keys: VisemeKey[]): void {
    this.timeline = [...keys].sort((a, b) => a.t - b.t);
  }

  update(delta: number): void {
    const playing = !this.audio.paused && !this.audio.ended;
    if (!playing) this.readout = "—";
    const targets = playing ? this.targets() : blank();
    for (const vowel of VOWELS) {
      const tau = targets[vowel] > this.weights[vowel] ? 0.04 : 0.09;
      const gain = 1 - Math.exp(-delta / tau);
      this.weights[vowel] += (targets[vowel] - this.weights[vowel]) * gain;
      this.vrm.expressionManager?.setValue(vowel, this.weights[vowel]);
    }
  }

  private targets(): Record<Vowel, number> {
    if (this.mode === "timestamp") return this.timestampTargets(this.audio.currentTime);
    return this.audioTargets();
  }

  private audioTargets(): Record<Vowel, number> {
    this.analyser.getByteTimeDomainData(this.time);
    this.analyser.getByteFrequencyData(this.freq);
    let energy = 0;
    for (const sample of this.time) {
      const centered = (sample - 128) / 128;
      energy += centered * centered;
    }
    const rms = Math.sqrt(energy / this.time.length);
    const open = clamp((rms - 0.008) * 22, 0, 1);
    const result = blank();
    if (open <= 0) return result;
    const rate = this.analyser.context.sampleRate;
    const low = band(this.freq, rate, this.analyser.fftSize, 250, 700);
    const mid = band(this.freq, rate, this.analyser.fftSize, 700, 1800);
    const high = band(this.freq, rate, this.analyser.fftSize, 1800, 3800);
    let vowel: Vowel = "aa";
    if (high > mid * 1.05 && high >= low) vowel = high > mid * 1.35 ? "ih" : "ee";
    else if (low > mid) vowel = low > high * 1.2 ? "ou" : "oh";
    result.aa = open;
    if (vowel !== "aa") result[vowel] = open * 0.45;
    this.readout = `音频 ${vowel} ${open.toFixed(2)}`;
    return result;
  }

  private timestampTargets(time: number): Record<Vowel, number> {
    const result = blank();
    let chosen: VisemeKey | null = null;
    for (const key of this.timeline) {
      if (key.t > time) break;
      chosen = key;
    }
    if (!chosen) {
      this.readout = "时间轴 —";
      return result;
    }
    result[chosen.v] = clamp(chosen.w, 0, 1);
    this.readout = `时间轴 ${chosen.v} ${result[chosen.v].toFixed(2)}`;
    return result;
  }
}

function blank(): Record<Vowel, number> {
  return { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
}

function band(bins: Uint8Array, sampleRate: number, fftSize: number, lowHz: number, highHz: number): number {
  const hz = sampleRate / fftSize;
  const start = Math.max(0, Math.floor(lowHz / hz));
  const end = Math.min(bins.length - 1, Math.ceil(highHz / hz));
  if (end < start) return 0;
  let sum = 0;
  for (let index = start; index <= end; index += 1) sum += bins[index] ?? 0;
  return sum / (end - start + 1) / 255;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
