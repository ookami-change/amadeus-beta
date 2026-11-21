# 试听反馈服务

给负责人做盲听用。页面放在公网上，负责人在手机或电脑上听完、点选，结果存在服务器上，架构师读出来。负责人 2026-11-22 定的做法。

服务器是负责人的 AWS 机器。仓库是公开的，**这里不写服务器地址**，地址记在 agent-bus 的 KV `infra/listen-server`。

## 组成

| 文件 | 作用 |
|---|---|
| `server.py` | 只用标准库。托管 `pages/<slug>/` 下的页面，把反馈存进 `data/<slug>/feedback.json`，同时每次追加到 `history.jsonl`。音频支持 Range 请求，Safari 播放需要这个。全站 `noindex`，`/robots.txt` 禁止所有爬虫 |
| `page/index.html` | 通用试听页模板。题目从嵌入的 manifest 读取。每版回答「还是她吗」「情绪到位吗」两个问题，每组可以选最好的一版、写备注；点「完成」后才能揭晓参数。页脚标注「AI 合成」（MiniMax 协议第 1.7 条） |
| `make_page.py` | 用一份 spec 生成页面目录。音频改成 `audio/01.wav` 这种编号名，听的人看文件名猜不出是哪一版 |
| `deploy/deploy.sh` | 三个用法：`install`（装服务和 Caddy）、`publish`（发布一页）、`feedback`（读结果） |
| `deploy/amadeus-listen.service` | systemd 服务：独立用户、只读文件系统、只能写 `data/`、只监听 127.0.0.1:8080 |
| `deploy/Caddyfile` | Caddy 自动申请 HTTPS 证书，反代到 8080 |

## 发布一期试听

1. 写 spec。格式和 manifest 一样，只是 `src` 写成音频文件的路径（相对 spec 文件，也可以写绝对路径）：

   ```json
   {
     "title": "页面标题",
     "heading": "大标题",
     "lede": "一段说明",
     "steps": ["怎么听，第一步", "第二步"],
     "reference": {"label": "她平时的声音", "src": "../out/minimax-turbo/02.wav"},
     "lines": [
       {
         "id": "10",
         "title": "第 10 条",
         "text": "台词",
         "hint": "想要的效果",
         "refs": [{"label": "上一轮原版", "src": "...", "reveal": "揭晓后显示的参数"}],
         "takes": [{"src": "...", "reveal": "揭晓后显示的参数"}]
       }
     ]
   }
   ```

   `takes` 的顺序就是页面上甲乙丙丁的顺序，**先自己打乱**，打乱后的对应关系记进 bus 的 KV。不写 `questions` 就用默认的两个问题。

2. 生成页面，再发布：

   ```bash
   uv run --directory evals python -m listen.make_page spec.json /tmp/page   # 输出里有一个建议的 slug
   HOST=ubuntu@<服务器> evals/listen/deploy/deploy.sh publish /tmp/page <slug>
   ```

   页面地址是 `https://<站点>/l/<slug>/`。slug 是随机串，不知道链接的人打不开。

3. 读结果：

   ```bash
   HOST=ubuntu@<服务器> evals/listen/deploy/deploy.sh feedback <slug>
   ```

## 首次安装

```bash
HOST=ubuntu@<服务器> SITE=<域名> evals/listen/deploy/deploy.sh install
```

AWS 安全组要放行 80 和 443。站点名放在服务器的 `/etc/default/caddy` 里，换域名时改这一行，再 `sudo systemctl restart caddy`。

## 回滚

```bash
sudo systemctl disable --now amadeus-listen caddy
sudo apt remove caddy
sudo rm -rf /srv/amadeus-listen /etc/systemd/system/amadeus-listen.service
```

然后在安全组里关闭 80 和 443。
