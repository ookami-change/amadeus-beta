# 真机测量模式（R4）

形象演示上叠一层引导面板，用页面自己的 rAF 循环测帧率，测完把报告 POST 到页面旁边的 `api/report`。渲染参数、待机、视线、口型逻辑都不动，测的就是当前这版演示。

## 用法

```bash
pnpm download:vrm
pnpm --filter @amadeus/web build:bench     # VITE_BENCH=1，--base ./，输出 apps/web/dist/
HOST=ubuntu@<服务器> evals/listen/deploy/deploy.sh publish apps/web/dist <slug>
```

- 读报告：`curl https://<站点>/l/<slug>/api/report`，返回 JSON 数组，每次提交一条。
- 开发时在地址后加 `?bench` 也能打开测量模式；不加时页面和平常一样。
- 本地自测：把 dist 拷到 `<root>/pages/<slug>/`，运行 `python3 evals/listen/server.py --root <root> --port 8080`，打开 `http://127.0.0.1:8080/l/<slug>/`。

## 流程

1. 待机 60 秒（先尝试进入全屏）
2. 连播 3 次示例台词，期间每 250ms 记一次口型读数
3. 用手指或鼠标划动 10 秒，看视线
4. 回答三个问题，设备名和备注可选，然后提交

## 报告字段（`version: 1`）

| 字段 | 含义 |
|---|---|
| `kind`, `version`, `page` | 固定为 `r4-bench`；格式版本；页面路径 |
| `device.userAgent`, `device.uaData` | UA，以及 Chromium 的高熵 UA 数据（机型、系统版本等）。拿不到就是 null |
| `device.gpu` | WebGL 的 UNMASKED renderer；拿不到时用 RENDERER |
| `device.pixelRatio`, `bufferWidth/Height` | three.js 渲染器的像素比（演示固定为 1），以及实际的绘制缓冲尺寸 |
| `device.screen`, `devicePixelRatio`, `viewport` | 屏幕、DPR、视口 |
| `device.hardwareConcurrency`, `deviceMemory`, `localTime`, `timeZone` | CPU 线程数、内存档位（GB，只有 Chromium 有）、本地时间（带偏移）、时区 |
| `deviceName`, `note` | 用户自己填的，可以为 null |
| `answers.loop` | `none` 看不出 / `some` 有点 / `obvious` 明显 |
| `answers.gaze` | `natural` 自然地跟 / `stiff` 跟但僵 / `none` 没动 |
| `answers.mouth` | `clear` 清楚 / `weak` 勉强 / `none` 没动 |
| `windows[]` | 三个采样窗口：`idle`、`playback`、`gaze` |
| `windows[].frames`, `durationMs` | 帧间隔个数、窗口时长 |
| `windows[].valid` | 窗口内页面没被切到后台，也没失焦，才是 true。无效窗口不计入结论 |
| `windows[].fpsMedian`, `fpsP5` | 按 500ms 分桶算 FPS（和 HUD 一样），取中位数和 P5，最近秩 |
| `windows[].frameMsMedian`, `frameMsP95`, `frameMsMax` | 帧间隔的中位数、P95、最大值，单位 ms |
| `windows[].longFrames` | 超过 50ms 的帧数 |
| `windows[].deltasMs`, `downsampled` | 原始帧间隔，保留 1 位小数；超过 9000 个时等距降采样，并把 `downsampled` 设为 true |
| `windows[].mouth` | 只在 playback 窗口有：`samples` 是口型读数序列，`maxWeight` 是模型口型表情权重的最大值。如果权重到了 1 而嘴看起来不动，问题在模型的表情绑定，不在口型逻辑 |

单份报告通常十几 KB，服务器上限是 256 KiB。

## 注意

- headless 或自动化浏览器里，页面可能被当成在后台，rAF 会停或被限帧。这时窗口会标成 `valid: false`，或者帧率偏低。判断达标与否，只用真人在普通浏览器里测的数据。
- 报告里没有 IP、地理位置或账号信息。拿到链接的人都能读 `api/report`。
