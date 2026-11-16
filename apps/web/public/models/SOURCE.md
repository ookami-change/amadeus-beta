# 示例模型

M0 形象验证用的是 VRoid Studio β 的官方示例 **Vita**（β Ver AvatarSample_3）。

- 作者：pixiv Inc.
- 许可：CC0。pixiv 放弃了该示例模型的版权。
  - 说明：<https://vroid.pixiv.help/hc/en-us/articles/4402614652569>
  - 模型页：<https://vroid.pixiv.help/hc/ja/articles/360014900113>
- 文件来源：社区镜像 `madjin/vrm-samples` 中的 `vroid/beta/Vita.vrm`（与官方 β 示例为同一份 CC0 模型）。
- 获取：在仓库根目录运行 `pnpm download:vrm`。这份镜像约 14MB，不入库。

这份镜像是 VRM 0.x。`three-vrm` 会按 0.x 加载并旋转到当前坐标系。公开渠道里同一角色的 VRM 1.0 转换件在 VRoid Hub 上，没有不登录就能取的稳定文件地址，所以演示用这份 CC0 原件。若换上 VRM 1.0 文件，放到 `public/models/avatar.vrm` 即可，加载器两种都认。

角色不是克劳德。克劳德的正式模型还没到，这里只验证渲染、待机和口型。
