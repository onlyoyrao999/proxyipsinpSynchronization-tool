# Cloudflare IP Auto Scanner & Sync (CF Proxyip 扫描 自动同步器)

这是一个运行在 GitHub Actions 上的全自动 Cloudflare  IP 扫描与 DNS 同步工具。
它会自动生成大量 CF IP 并通过 API 测速挑选出最优的 Proxyip，并自动更新到你的 Cloudflare DNS 记录中。同时具备**自适应热点网段学习**功能，大幅提升扫描效率。

---

## 🚀 部署与配置

### 1. 配置 GitHub Secrets (必须)
要想让脚本正常运行，你需要进入你的 GitHub 仓库：
点击右上角的 **Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret**，依次添加以下 5 个环境变量：

| Secret 名称 | 说明 | 示例 |
| --- | --- | --- |
| `CF_EMAIL` | 你登录 Cloudflare 的邮箱账号 | `your@email.com` |
| `CF_API_TOKEN` | Cloudflare 的 Global API Key (全局 API 密钥) | `8a9b...` |
| `CF_ZONE_ID` | 你域名的 区域 ID (在 CF 域名概述页面右下角，32位字符串) | `9841...` |
| `CF_TARGET_DOMAIN` | 你的主域名（程序不仅会将所有神仙 IP 同步到主域名，还会根据地区自动拼接子域名，如 `sjc.yourdomain.com`） | `yourdomain.com` |
| `CHECK_API_URL` | 验活接口的**域名**（只存 Secrets，不写进代码；只填域名就行） | `your-check-host.pages.dev` |

配置好后，你可以点击仓库顶部的 **Actions** -> 选中 **Cloudflare IP Auto Scanner & Sync** -> 点击 **Run workflow** 来手动触发一次运行，检查是否正常。

### 2. `CHECK_API_URL` 怎么填（验活接口说明）

本仓库**不内置任何验活接口地址**，代码里没有写死地址，唯一入口就是上面这个 Secret。所以：

- **Fork 本仓库后，必须填你自己的验活接口地址**，不填程序会直接报错停跑（日志里会出现 `Error: CHECK_API_URL is not set ... Aborting.`），这是正常现象，不是程序坏了。
- **填法**：Secret 名称 `CHECK_API_URL`，**值只填你自己验活接口的域名就行**，例如 `your-check-host.pages.dev`——不用带 `https://`、不用带 `/check`、不用带 `?proxyip=`，脚本会自动拼成完整的验活地址。（万一填了完整地址也能自动识别，不影响使用。）以后换接口，改这一个 Secret 的域名即可，代码不用动。
- **接口协议要求**（你自己的接口需满足这个格式，脚本才认）：

  - 请求（脚本用你填的域名自动拼出）：`GET https://<你填的域名>/check?proxyip=<要测的IP>`
  - 返回 JSON 示例：

    ```json
    {"success": true, "dataCenter": "SJC", "latencyMs": 19}
    ```

  - 字段说明：`success` 必须为 `true` 才算这个 IP 可用；`dataCenter`（或 `colo`）是机场三字码（如 `SJC`、`LAX`），脚本靠它筛选地区；`latencyMs`（或 `tcpDuration`）是延迟毫秒数，可选，不返回时脚本用整个请求的耗时代替。
- **先自测再填**：在浏览器里打开 `https://<你的域名>/check?proxyip=1.1.1.1`，能返回带 `success` 字段的 JSON，就说明接口格式对，把这个域名填进 Secret 即可。
- **和另外 4 个 Secret 的区别**：`CF_EMAIL` / `CF_API_TOKEN` / `CF_ZONE_ID` / `CF_TARGET_DOMAIN` 不填只是跳过 DNS 同步、扫描照常进行；只有 `CHECK_API_URL` 是硬性必填，不填不扫。

---

## ⚙️ 高级自定义设置

### 🌍 1. 全局负载均衡：主域名+子域名双重同步 (新功能!)
现在的同步逻辑更加强大，并且你拥有绝对的控制权：
- **子域名细分**：扫出来的极品 IP，依然会自动根据地区推送到对应的子域名上（例如 `lax.yourdomain.com`、`sjc.yourdomain.com`）。
- **主域名汇总开关**：你可以通过代码里的 `SYNC_MAIN_DOMAIN` 开关自由控制。开启后，**所有地区**扫出来的神仙节点会被全盘汇总，并统一推送到你的**主域名**上，让主域名拥有全球负载均衡的能力！如果设置为 `NO` 则不会影响你的主域名。

### 🧠 2. 核心亮点：三级扫描策略与自动全网探索
本次加入了强大的**全自动探索脚本 (`mine-cidrs.yml`)**，在每周日自动巡查验证全网，不断为你沉淀真实的优质精品段：
1. **老牌验证 (去失效段)**：不仅在每次主程序中，甚至在定期探索里，都会严格重新测试 `hot_cidrs.txt` 里留存的优选网段。扫不到或者归属地不清晰 (`#UNK`) 的段会被**自然淘汰**，绝不留死段。
2. **精品段萃取 (`hot_cidrs.txt`)**：主程序在非全局模式下，如果在旧库 (`ips-v4.txt`) 没凑够 IP，就会立刻扑向你千锤百炼积攒下来的 `hot_cidrs.txt` 精品段中重点搜索，**出货率极高**！
3. **全球新段兜底 (`ip.txt`)**：当优质段都没凑齐，或者在定时探索时，程序最后会向 `ip.txt` 里的海量官方大网段撒网盲抽，发现的神仙网段会自动汇总沉淀至你的 `hot_cidrs.txt` 中。

### 📍 3. 全新多区并发与动态域名同步 (支持全区盲扫 ALL)
本次重磅升级了**多地区智能同步**功能：
打开 `scripts/cf_scanner_sync.py`，在代码的最顶部（大约第 14 行）找到全局配置区：
```python
# ==========================================
# 🎯 全局默认地区设置 (如果想要永久换地区，只改这里！)
# 支持多个地区，用逗号隔开，例如 "SJC,LAX,HKG,FRA,NRT"
# 💡 新手不知道有什么地区？可以直接填 "ALL"，系统会全区盲扫并自动创建所有能扫到的地区子域名！
# ==========================================
DEFAULT_REGIONS = "SJC"

# 🌐 主域名终极大汇总同步开关
# 设置为 "YES": 开启！将所有扫到的极品节点汇总推送到你的主域名（全球负载均衡）
# 设置为 "NO": 关闭！仅同步到各个地区子域名，不修改主域名的解析记录
SYNC_MAIN_DOMAIN = "NO"

# 🎯 扫描与同步数量设置
# 控制每个地区最终要同步几个 IP 到 Cloudflare DNS (默认 10 个)
SYNC_COUNT = 10
# 控制 ALL 全局模式下，最终要扫出多少个 IP 才停止 (默认 10 个)
ALL_MODE_LIMIT = 10
# ==========================================
```
- **精准筛选模式**：填入你想要的机场码（如 `SJC,LAX,HKG`），系统会严格执行筛选标准，并**自动分配同步到各自的子域名**（如 `sjc.你的主域名`）。
- **盲扫模式 (ALL)**：直接填 `"ALL"`！系统会在全网疯狂盲扫，只要凑够设定的数量（`ALL_MODE_LIMIT`，默认10个）瞬间光速收网！**在此模式下，扫到的 IP 只会全部统一同步到 `CF_TARGET_DOMAIN` 设置的主域名，不会自动创建地区子域名。**
- **免配置测速模式**：如果你只是想体验测速、或者没有 Cloudflare 域名，可以只填 `CHECK_API_URL` 这一个 Secret，不填那 4 个 Cloudflare 的 Secret 也能运行，程序会自动跳过 DNS 同步、只输出扫描结果。注意 `CHECK_API_URL` 本身是必填的（见上方第 2 节），不填会直接停跑。

### ⚡ 4. 扫描逻辑与同步数量设置 (全在 Python 里设置)
打开 `scripts/cf_scanner_sync.py`，你会在刚刚那个全局设置的下方看到这两个参数：
- **`SYNC_COUNT`**: 控制**每个精准地区**最终要同步几个 IP 到 Cloudflare 的 DNS 记录（默认 `10` 个）。如果你设置了 6 个地区，单次运行总计会挑出 60 个极品节点分门别类推送。
- **`ALL_MODE_LIMIT`**: 专门用于控制 `ALL` 全局盲扫模式下，总计要抓多少个优质 IP 就立刻停止（默认 `10` 个）。

如果遇到速度慢或报错，你可以调整并发数量（默认 `50` 线程），在 `scripts/cf_scanner_sync.py` 的 `max_workers=50` 处修改。调高可加快测速，但需注意 API 接口的抗压能力。

### 🌐 4. 如何配置自己想要的 IP 段 (CIDR)？
现在无需修改代码！直接打开代码仓库根目录下的 **`ip.txt`** 文件。
里面默认存放了 Cloudflare 官方的 IPv4 核心网段：
```text
104.16.0.0/13
104.22.0.0/16
104.23.0.0/16
...
```
如果你在网上找到了某些玄学好用的 CF IP 段（或者你想扫其他特定的网段），直接把网段（如 `198.41.128.0/17`）另起一行加到这个文件里即可。程序会自动读取这个文件，并在其中随机抽卡测速。
