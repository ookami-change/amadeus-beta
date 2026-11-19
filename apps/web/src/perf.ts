/** Frame timing for the M0 performance readout. The numbers are this page's own rAF loop. */

export class PerfMonitor {
  private readonly fpsNode: HTMLElement;
  private readonly frameNode: HTMLElement;
  private readonly gpuNode: HTMLElement;
  private last = performance.now();
  private windowStarted = performance.now();
  private frames = 0;
  private sum = 0;
  private lastFrame = 0;

  constructor(fpsNode: HTMLElement, frameNode: HTMLElement, gpuNode: HTMLElement) {
    this.fpsNode = fpsNode;
    this.frameNode = frameNode;
    this.gpuNode = gpuNode;
  }

  setGpu(renderer: string): void {
    this.gpuNode.textContent = renderer;
  }

  tick(now: number): number {
    const deltaMs = now - this.last;
    this.last = now;
    this.lastFrame = deltaMs;
    this.frames += 1;
    this.sum += deltaMs;
    if (now - this.windowStarted >= 500) {
      const fps = (this.frames * 1000) / this.sum;
      const average = this.sum / this.frames;
      this.fpsNode.textContent = fps.toFixed(0);
      this.frameNode.textContent = `${average.toFixed(1)} ms（上一帧 ${this.lastFrame.toFixed(1)}）`;
      this.frames = 0;
      this.sum = 0;
      this.windowStarted = now;
    }
    return Math.min(deltaMs / 1000, 0.05);
  }
}
