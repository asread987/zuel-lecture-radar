# ZUEL 讲座雷达

> 可配置的定时任务系统：自动从中南财经政法大学各学院官网 / 微信公众号渠道抓取**即将开始的**讲座信息，
> 汇总为结构化清单，**优先突出经济学相关讲座**，并按每位用户自己的节奏推送到微信。

---

## 一、它做什么

**只找「还没开始」的讲座。** 已经办过的、正在进行的，一律不收——因为抓讲座的意义在于还能去听。

```
每天 20:00  ──►  逐用户判断「距上次推送是否已满 interval_days 天」
                        │
                        ├── 未满 ─► 跳过
                        └── 已满 ─► 采集信息源 → 抽取字段 → 海报OCR → 过滤已结束
                                        │                             │
                                        │                    ┌────────┴────────┐
                                        │                已结束/已过期      还没开始
                                        │                   丢弃              保留
                                        └──► 生成报告(MD/HTML/JSON/CSV) → 推送微信
```

报告形态（开头先说清哪些学院有更新，明细在下面，末尾给全渠道状态）：

```markdown
# 中南财经政法大学 · 即将开始的讲座（2026-09-20）

3 个渠道有更新，共 7 条（经济学相关 6 条）：
- 公共管理学院 · 学术活动 — 3 条
- 财政税务学院 · 学术讲座 — 3 条
- 哲学院 · 学术动态 — 1 条

## 一、经济学相关讲座（6 条）
### 1. 讲座预告 | "南湖红帆 · Cityπ高端论坛第6期"  `今天开始 · 海报OCR`
- 时间：2026年9月20日 14:30     ← 从海报图片里 OCR 出来的
- 地点：文泉楼北603会议室
- 主办方：…  原文：…

## 二、其他学科讲座（… 条）
…
### 渠道抓取状态（共 19 个：已更新 3，抓取失败 0）
- 会计学院 · 学术讲座（官网）— 未更新讲座信息
- 财政税务学院 · 学术讲座（官网）— 已更新 3 条（经济学 3 条）
- 经济学院 · 学术讲座（官网）— 有 1 条讲座，但均已结束
- 金融学院 · 学术交流（官网）— 抓取失败：…
```

四位用户各自独立的规则：

| 用户可自定义 | 配置项 | 示例 |
| --- | --- | --- |
| 抓取间隔天数 | `interval_days` | `3` 天 / `14` 天 |
| 目标信息源范围 | `sources` | `all` 或 `[经济学院, 金融学院]` 或 `[jjxy-lectures]` |
| 推送到的微信账号 | `channels` | Server酱 / PushPlus / WxPusher / 企业微信 |
| 是否仅筛选经济学讲座 | `econ_only` | `true` / `false`（false 时经济学仍置顶突出） |
| 日期读不出来时怎么办 | `unknown_date_policy` | `keep` 保留并标注 / `drop` 丢弃 |

输出清单每条都包含：**讲座标题、时间、地点、主办方、原文链接**，
另附主讲人、开场状态（如「09-28 开始」）、发布日期、来源栏目、经济学判定依据。

### 1.1 「还没开始」是怎么判出来的

判定顺序（`classify.upcoming_status`）：

1. **能解析出讲座日期** → 日期早于今天（可配 `upcoming_grace_days` 留余地）就丢弃，否则保留并标注状态。
2. **日期读不出来**（正文是海报且 OCR 也没读出时间）：
   - 是「回顾类」报道 → 丢弃；
   - `unknown_date_policy: drop` → 丢弃；
   - `unknown_date_policy: keep` → 再看发布日期，发布已超过 `unknown_date_max_age_days` 天
     视为过期丢弃，否则保留并标注「时间待确认（见海报/原文）」。

要让第 1 步生效，**日期必须能读出来**，这中间有三个坑，都已处理：

- 列表页只显示「04-27」不带年份 → 改从**文章 URL** 里取（Bode 的 URL 形如 `/2026/0427/...`，年份总是完整的）。
- 正文写「4月23日上午」不带年份 → 用**发布年份**补全；若补出来比发布日期早 180 天以上，
  判定为「年末发次年年初的预告」，年份 +1。
- 正文是**海报图片**、DOM 里没有文字 → 用 OCR，见下条。

### 1.2 海报 OCR：把图里的「讲座时间/地点」读出来

ZUEL 大量讲座预告的正文就是一张海报图片，DOM 里**一个字都没有**。
系统用 **macOS 内置的 Vision 框架**做中文 OCR（走 `osascript -l JavaScript` 的 ObjC 桥），
**不需要 pip 装任何东西**，也不依赖 tesseract 之类的系统二进制。

实测效果（真实海报）：

```
讲座时间：2026年9月28日（周一）14:00-15:30   →  时间=2026年9月28日（周一）14:00-15:30
讲座地点：文泉楼北603会议室                  →  地点=文泉楼北603会议室
主办单位：中南财经政法大学财政税务学院        →  主办方=中南财经政法大学财政税务学院
```

- 首次识别约 **1 秒/张**（含下载），结果按图片 URL 缓存到 `data/ocr_cache/`，重跑不再重复识别。
- 一次性把所有待识别的图片喂给**同一个 osascript 进程**，摊薄进程启动开销。
- 非 macOS、或 osascript 不可用、或识别失败 → 自动降级为空，不影响整轮抓取，
  条目会照常保留并标记「时间待确认」。用 `ocr_poster: false` 可整体关闭。

---

## 二、关于「微信公众号」渠道的实话

需求里提到「官方微信公众号」。这里必须如实说明技术现实：

- **微信公众号没有公开的内容 API**，个人也无法通过爬虫稳定获取——`mp.weixin.qq.com`
  正文页需要有效的 `__biz` + 签名参数，而公众号历史文章列表接口（`appmsg`）已被风控封死。
  网上常见的「搜狗微信搜索」方案反爬极严、随时失效，**不适合放进无人值守的定时任务**。
- **好消息是：各学院的公众号内容与官网讲座栏目高度重合**。学院发布讲座的规范动作就是
  官网「学术讲座」栏目 + 公众号双发，而官网是**稳定可抓**的。因此本系统默认以官网为主力渠道。
- 如果确实需要公众号原文，本系统预留了三条可用路径（`config/sources.yaml` 里有现成模板）：
  1. **RSSHub / wechat2rss 自建** —— 把公众号转成标准 Feed，用 `type: rss` 配置。这是最稳的方案。
  2. **手工登记** —— `type: wechat_manual`，填入确知的 `mp.weixin.qq.com` 文章链接。
  3. **搜狗微信兜底** —— `type: sogou_wechat`，按关键词检索，稳定性差，默认关闭。

---

## 三、目录结构

```
zuel-lecture-radar/
├── run.py                          # 命令行入口（doctor / sources / users / init-user /
│                                   #   test-push / run / due / all / logs）
├── requirements.txt
├── LICENSE                         # MIT + 分发使用须知
├── .gitignore                      # 已排除 data/ out/ .venv/ 与 config/users/（含密钥）
├── config/
│   ├── sources.yaml                # 信息源库：20 个已实测的官网讲座栏目 + 公众号模板
│   ├── user-template.yaml          # 用户配置模板（init-user 据此生成，随版本分发）
│   └── users/                      # 每位用户一份配置（整个目录已被 git 忽略）
│       ├── demo.yaml               # 完整参数示例
│       ├── alice.yaml              # 只关心经济学，每 3 天，只订阅经济类学院
│       └── bob.yaml                # 全学科，每 14 天，只落地文件
├── zuel_radar/
│   ├── config.py                   # 配置加载 + 校验
│   ├── models.py                   # Lecture 数据模型
│   ├── fetcher.py                  # 抓取层：HTTP 优先 + 无头 Chrome 兜底（WAF 绕过）
│   ├── sources/
│   │   ├── __init__.py             # 源注册表与统一采集接口
│   │   ├── bode.py                 # 博达 CMS 列表页 / 详情页解析
│   │   ├── rss.py                  # RSS/Atom（承接公众号 Feed）
│   │   └── wechat.py               # 微信文章正文解析 + 搜狗检索
│   ├── extract.py                  # 从正文抽 时间/地点/主办方/主讲人 + 预告or回顾 + 跨年日期修正
│   ├── classify.py                 # 经济学相关性加权评分 + 噪声过滤 + 「还没开始」判定
│   ├── ocr.py                      # 海报 OCR（macOS 内置 Vision 框架，零额外依赖）
│   ├── store.py                    # SQLite：文章库、按用户去重、推送状态、日志
│   ├── report.py                   # 报告渲染：Markdown / HTML / JSON / CSV
│   ├── notify/                     # 推送渠道（serverchan / pushplus / wxpusher / wecom / …）
│   └── scheduler.py                # 「间隔 N 天」到期判定
├── scripts/
│   ├── setup.sh                    # 一键安装（建环境 + 装依赖 + 自检 + 生成用户配置）
│   ├── install_schedule.sh         # 跨平台定时任务入口（macOS→launchd，Linux→cron）
│   ├── install_launchd.sh          # macOS launchd 的具体实现
│   ├── com.zuel.lecture-radar.plist
│   └── discover_sources.py         # 学院改版后重新探测讲座栏目 ID
├── data/                           # 缓存 + SQLite 库 + OCR 缓存（运行时生成，不进仓库）
└── out/                            # 报告产物（运行时生成，不进仓库）
```

---

## 四、快速开始

**最省事的方式**——一条命令把环境装好，并生成一份待填的用户配置：

```bash
bash scripts/setup.sh 你的标识
```

装完之后只有两件事要做（见 [4-3 节](#四之三分发给其他人使用) 的三步说明）：

```bash
# 编辑 config/users/你的标识.yaml，把 channels 换成自己的推送渠道，然后：
.venv/bin/python run.py test-push --user 你的标识     # 微信能收到测试消息就通了
bash scripts/install_schedule.sh                     # 装上定时任务
```

**想逐步来也可以**（`setup.sh` 做的就是这些）：

```bash
cd zuel-lecture-radar

# 1) 装依赖
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2) 环境自检（会检查浏览器通道与 OCR，这两项决定抓取质量）
.venv/bin/python run.py doctor

# 3) 看一眼内置了哪些信息源
.venv/bin/python run.py sources

# 4) 建一个自己的用户配置
.venv/bin/python run.py init-user 你的标识
#    然后编辑 config/users/你的标识.yaml：填 interval_days、sources、channels

# 5) 先干跑一次，确认报告内容符合预期（不推送、不记录已送达）
.venv/bin/python run.py run --user 你的标识 --dry-run

# 6) 确认无误后正式运行一次
.venv/bin/python run.py run --user 你的标识

# 7) 装定时任务（见下一节）
```

---

## 四之二、定时任务：两种触发器

「每间隔 N 天、在触发当天 20:00 推送」这个规则，实现方式是
**每天 20:00 唤起一次调度器，由调度器逐用户判断是否到期**。
这样一位用户设 3 天、另一位设 14 天都能成立，不需要为每位用户建一条定时任务。

### 触发器 A：系统级定时任务（推荐，机器开着就一直在跑）

```bash
bash scripts/install_schedule.sh              # 每天 20:00（macOS→launchd，Linux→cron）
bash scripts/install_schedule.sh install 21 30   # 或自定义时刻
bash scripts/install_schedule.sh uninstall    # 卸载
```

- **macOS**：写入 `~/Library/LaunchAgents/com.zuel.lecture-radar.plist`
- **Linux**：往 `crontab` 里加一行（带 `# zuel-lecture-radar` 标记，便于重复安装与卸载）
- **Windows**：脚本会打印现成的 PowerShell `Register-ScheduledTask` 命令

脚本会自动挑选已装好依赖的 Python 解释器（优先用项目自带的 `.venv`），不需要你填绝对路径。

> **macOS 注意**：`launchctl` 只能在你本人登录的图形会话里注册任务。
> 如果安装时提示 `Bootstrap failed: 5: Input/output error`，
> 说明不是在正常终端里执行（比如从某个沙箱化的工具里跑的）——请自己打开「终端」重跑一次：
> ```bash
> cd <项目目录> && bash scripts/install_schedule.sh
> ```
> 验证：`launchctl print gui/$(id -u)/com.zuel.lecture-radar | head -30`

### 触发器 B：WorkBuddy 内的定时任务

已内置一条名为 **「ZUEL 讲座雷达 · 每日 20:00 调度」** 的定时任务，
每天 20:00 执行 `run.py due`。适合不想碰系统级配置、且习惯在 WorkBuddy 里看结果的情况。

两个触发器可以同时存在：`run_user()` 带进程级互斥锁（`data/.run.lock`），
加上「间隔 N 天」判定本身是幂等的，所以**不会重复推送**。

---

## 四之三、分发给其他人使用

### 先想清楚走哪条路

| 路径 | 适合场景 | 代价 | 现状 |
| --- | --- | --- | --- |
| **代码包 + 每人本地部署** | 同学 / 同门 / 课题组几个人 | 每人花 3 分钟装一次；各自绑各自的微信 | ✅ **已经完全就绪，推荐** |
| 在线服务（打开网页就能用） | 面向不特定多数人 | 要另写 Web 层 + 托管 + 多租户密钥管理 + 抗封 | ❌ 未做，且不建议（原因见下） |
| Docker 镜像 | 想一条命令跑起来 | 需要 Docker 环境 | ❌ 未做（本机没有 Docker，我无法构建验证，不会给你没验证过的东西） |

**为什么推荐「每人本地部署」而不是做成在线服务**，三条都是硬约束：

1. **推送凭据天然就该一人一份**。微信个人号没有官方推送接口，能用的都是「让本人扫码把自己的微信用
   Server酱 / PushPlus / WxPusher 绑起来」——这本身就是每人一次的个人动作，集中托管反而要额外解决密钥安全。
2. **所有人共用一个出口 IP 去抓学校站点，很快会被 WAF 封**，而且对学校服务器不礼貌。
   分布式部署天然把请求量摊开了。
3. **抓到的内容版权归各学院**，做成公开服务等于替别人转发学校内容，有合规风险。

### 别人拿到之后怎么用（三步，约 3 分钟）

```bash
# 1) 拿到代码（git clone 或解压 zip）
git clone <仓库地址> zuel-lecture-radar && cd zuel-lecture-radar

# 2) 一键安装：建虚拟环境 → 装依赖 → 环境自检 → 生成一份用户配置
bash scripts/setup.sh 张三

# 3) 打开 config/users/张三.yaml，把 channels 换成自己的推送渠道（见下），然后：
.venv/bin/python run.py test-push --user 张三      # 微信能收到测试消息就通了
.venv/bin/python run.py run --user 张三 --dry-run  # 先干跑看报告内容
bash scripts/install_schedule.sh                   # 装上定时任务，收工
```

`setup.sh` 会把所有机器相关的路径都就地解析（不写死任何绝对路径），
所以别人克隆到任何目录都能直接用。

### 「填微信号推送」的正确理解

微信没有「填个微信号就能推」的官方接口，实际填的是**中转服务的凭据**，
每人扫一次码把自己微信绑上去即可：

| 渠道 | 怎么绑 | 适合 |
| --- | --- | --- |
| Server酱 | sct.ftqq.com 扫码 → 拿 `SendKey`（形如 `SCTxxxx`） | **最省事，个人首选** |
| PushPlus | 个人中心拿 `token`，可用 `topic` 把一组人拉进同一群组 | 想几个人进一个群 |
| WxPusher | 建应用拿 `app_token`，每人一个 `uid` | **多人共用一台机器时首选** |
| 企业微信机器人 | 群机器人 → `webhook` | 课题组 / 团队 |

**给课题组配一台常开机器的情况**（这是「多用户独立使用」最实用的落地方式）：
用 WxPusher，一个 `app_token` 下挂多个 `uid`，每位成员一份 `config/users/<名字>.yaml`，
只有 `uids` 和 `interval_days` 不同 —— 一份程序、大家各收各的、间隔各自独立。

### 跨平台能力对照

| 能力 | macOS | Linux | Windows |
| --- | --- | --- | --- |
| 抓取（无头 Chrome 绕过 WAF） | ✅ | ✅ 需装 Chrome/Chromium | ✅ 需装 Chrome |
| **海报 OCR**（读图里的时间地点） | ✅ 系统内置 Vision | ❌ 自动降级 | ❌ 自动降级 |
| 定时任务 | launchd（`install_schedule.sh` 已内置） | cron（已内置） | 任务计划程序（脚本会打印现成命令） |

非 macOS 时 OCR 不可用，那些「正文是海报」的条目时间会读不出来，
系统会按 `unknown_date_policy` 处理。想更严格就把 `unknown_date_max_age_days` 调小
（例如 7），这样读不出日期的旧预告会更快被丢弃。

### 打包命令

```bash
# 方式一：推到 git 仓库（推荐，后续可更新）
git init && git add -A && git commit -m "ZUEL 讲座雷达 v1.0"
git remote add origin <你的仓库地址> && git push -u origin main

# 方式二：打成压缩包发给别人（已自动排除 data/ out/ .venv/ 和个人配置）
git archive --format=zip --prefix=zuel-lecture-radar/ -o /tmp/zuel-lecture-radar.zip HEAD
```

`.gitignore` 已把 `config/users/` 整个排除（那里有推送密钥），
并把 `data/`、`out/`、`.venv/` 一并排除。**但推到公开仓库前请自己再 `git status` 确认一遍**——
密钥泄露是不可逆的。

---

## 五、推送渠道：怎么「推送到指定微信账号」

微信个人号没有官方推送接口，实务上只有下面几条路，本系统都支持，**在同一个 `channels`
列表里可以并列配置多个，全部会收到**。

| type | 服务 | 拿什么 | 适用场景 |
| --- | --- | --- | --- |
| `serverchan` | [Server酱](https://sct.ftqq.com) | `key`（形如 `SCTxxxx`，扫码绑定微信后获得） | **最省事**，个人用 |
| `pushplus` | [PushPlus](https://www.pushplus.plus) | `token`，可选 `topic`（群组） | 想把多个人/多个微信号拉进一个群组 |
| `wxpusher` | [WxPusher](https://wxpusher.zjiecode.com) | `app_token` + `uids` | **多用户各自收**：每个微信一个 uid，精确投递 |
| `wecom` | 企业微信群机器人 | `webhook` | 课题组 / 团队，微信侧可接收 |
| `console` | 无 | — | 打印到终端，自测用 |
| `file` | 无 | `dir` | 只落地文件 |

配置示例（`config/users/alice.yaml`）：

```yaml
channels:
  - type: serverchan
    key: SCTxxxxxxxxxxxxxxxxxx
  - type: wxpusher            # 同时再推一份给指定微信号
    app_token: AT_xxxxxxxxxxxx
    uids: [UID_xxxxxxxxxxxx]
```

**多用户场景怎么配**：每位用户一个 `config/users/<标识>.yaml`，各填各的渠道凭据。
若用 Server酱，等于每人绑各自的微信、各拿各的 key；若用 WxPusher，一个 `app_token`
下挂多个 `uids`，就能实现「一份程序、多个微信号各自独立收到自己的那一份」。

> 企业微信群机器人的 markdown 正文上限 4096 字节，系统会自动按条目边界截断并注明；
> 完整版永远在 `out/` 的报告文件里。

---

## 六、配置详解

### 6.1 信息源库 `config/sources.yaml`

内置 **20 个已实测可抓的官网讲座栏目**（覆盖 16 个学院 + 研究生院），例如：

| 源 key | 学院 | 栏目 | 实测条数 |
| --- | --- | --- | --- |
| `jjxy-lectures` | 经济学院 | 学术讲座 | 21 |
| `csxy-lectures` | 财政税务学院 | 学术讲座 | — |
| `kjxy-lectures` | 会计学院 | 学术讲座 | 14 |
| `ggglxy-lectures` | 公共管理学院 | 学术活动 | 14 |
| `gsxy-lectures` | 工商管理学院 | 讲座信息 | 3 |
| `yjsy-wenlan-dajiangtang` | 研究生院 | 文澜大讲堂 | — |

栏目地址可用 `python scripts/discover_sources.py` 重新探测（学院改版后用它刷新即可）。

### 6.2 用户配置关键项

```yaml
interval_days: 7          # 每 N 天触发一次
push_time: "20:00"        # 触发当天的推送时刻
sources: [all]            # all / 源key / 分组名(学院名) / 类型

# ── 只留「还没开始」的讲座 ──────────────────────────────
upcoming_only: true       # 关掉则不再按时间过滤（会连已办过的也一起收）
upcoming_grace_days: 0    # 给「今天刚开始」的活动留的宽限天数
unknown_date_policy: keep # 日期读不出来时：keep 保留并标注 / drop 丢弃
unknown_date_max_age_days: 14   # keep 时，发布已超过 N 天的预告视为过期
ocr_poster: true          # 用 macOS Vision OCR 读海报上的时间/地点

econ_only: false          # true=只推经济学；false=经济学置顶+其他学科分区
econ_threshold: 3.0       # 经济学判定阈值，越大越严格
lecture_only: true        # 过滤掉招生/公示类非讲座内容
drop_review: false        # 额外丢掉「已举办」的回顾（upcoming_only 已能覆盖大部分）

max_items: 40
lookback_days: 30         # 首次运行（无历史状态）时向前回看多少天
detail_limit: 25          # 单次最多抓多少篇正文来抽时间/地点
```

---

## 七、命令行

```bash
run.py doctor                       # 环境自检（含浏览器通道与 OCR 可用性）
run.py sources                      # 列出信息源
run.py users                        # 列出用户 + 下次预计推送时间
run.py init-user <标识>              # 生成用户配置（从 config/user-template.yaml）
run.py test-push --user <标识>       # 发一条测试消息，验证推送凭据
run.py run  --user <标识> [--dry-run] [--no-push] [--force] [--today YYYY-MM-DD]
run.py due  [--ignore-time]         # 调度入口：只跑到期用户（定时任务调这个）
run.py all  [--dry-run]             # 所有用户
run.py logs                         # 最近推送记录
```

配套脚本：

```bash
bash scripts/setup.sh [标识]              # 一键安装（建环境+装依赖+自检+生成配置）
bash scripts/install_schedule.sh          # 装定时任务（macOS→launchd，Linux→cron）
bash scripts/install_schedule.sh uninstall # 卸载定时任务
bash scripts/discover_sources.py          # 学院改版后重新探测讲座栏目
```

`--today` 是个**回溯验证**开关：把「今天」设成指定日期，用来复现「只保留未开始的讲座」这条规则的效果。
例如今天是 9-30 但要确认 9-20 那天的报告长什么样：

```bash
run.py run --user bob --dry-run --today 2026-09-20
```

日常调度**不要**用它；报告文件名会带上这个日期，不会覆盖真实报告。

---

## 八、技术要点（踩过的坑都在这里）

### 8.1 官网有 JS 反爬挑战，必须用无头 Chrome

ZUEL 各站点前置了一层 JS 挑战：检测 `navigator.webdriver`、`HeadlessChrome|Puppeteer|Playwright`、
软件 WebGL 渲染器（`Mesa|llvmpipe|VMware|Software|Generic`），再用 `crypto.subtle` 校验后才把
正文注入 `#content_container`。用 `requests` 只会拿到约 46KB 的空壳页（**0 个文章链接**）。

绕法：`Chrome --headless=new` + 覆盖 User-Agent + `--virtual-time-budget`。实测约 **1.7～2.0 秒/页**。
抓取层（`fetcher.py`）会先用轻量 HTTP 试一次，被挡后记住该域名并自动切到浏览器，同时把结果缓存到
`data/cache/`，同一次运行内不重复渲染。

**两个必须遵守的实现约束**（改代码时勿踩）：

1. **不要给 Chrome 传 `--user-data-dir`。** 实测在本机 Chrome 156 / macOS 27 上，只要指定自定义
   profile 目录，Chrome 必然卡死（等 GoogleUpdater 唤醒后不返回），而不传则稳定 1.7s 完成。
   因此这里用默认 profile + `--incognito`（既复用已初始化的 profile，又不污染浏览记录）。
   副作用：浏览器通道共用默认 profile，**必须串行**（代码里用 `_browser_lock` 保证）。
2. **不要用 `subprocess.run(capture_output=True)`。** Chrome 会派生后台子进程继承 stdout 管道，
   导致管道迟迟不关闭、`subprocess.run` 死等。改用临时文件重定向。

> 若定时任务执行时你自己的 Chrome 正在打开，无头实例可能被接管而返回空——`doctor` 会给出提示，
> 系统也会自动重试一次。建议把定时任务时刻安排在你不常用浏览器的时间。

### 8.2 博达 CMS 的栏目结构

各学院官网统一使用博达 CMS：

```
栏目列表页  https://<学院>.zuel.edu.cn/<columnId>/list.htm   （分页 list2.htm、list3.htm …）
文章详情页  https://<学院>.zuel.edu.cn/<YYYY>/<MMdd>/c<columnId>a<articleId>/page.htm
```

解析列表页时有三个坑，都已处理，改代码时别踩回去：

1. **导航里也有指向文章的链接**（如「书记信箱」）。定位列表容器的策略两个极端都不能用：
   只取「最深」的合格容器会误选页脚的「友情链接」小方框；只取「链接最多」的会一路退到 `<body>`。
   现在的做法是取**「文章链接数接近全局最多（≥60%）的容器中最深的那个」**，
   外加一层标题噪声词过滤。
2. **同一个 `<li>` 里有 3 个指向同一篇文章的 `<a>`**（图片链接、标题链接、「更多」链接），
   其中图片链接没有文字。所以**必须在标题校验通过之后才登记去重**，
   否则图片链接会先占住 URL、真正带标题的那个被当成重复丢掉 —— 一页 14 条会解析成 **0 条**。
3. **列表页的日期常常不带年份**（只显示「04-27」）。**文章 URL 里的日期总是完整的**
   （`/2026/0427/...`），优先从 URL 取，否则下游无法判断讲座是否已过期。

### 8.3 空壳页会污染缓存（一个隐蔽的坑）

渲染失败时 Chrome 可能返回一个**体积不小（约 46KB，因为内嵌了挑战脚本）但没有实际内容**的页面。
如果只用「体积太小」来判定失败，它会被当成正常页面**写进缓存**，
于是之后每次读缓存都拿到空页 —— 表现为**某个渠道明明有内容，却一直报「未更新讲座信息」**。

因此 `fetcher.py` 里有一道独立闸门 `looks_like_content()`：
正常渲染的列表页有 100+ 个 `href`，空壳页是 0 个，以此区分。
读缓存时也用它做校验，发现污染就丢弃重抓。

### 8.4 字段抽取与经济学判定

- **字段抽取**（`extract.py`）：按「时间：」「地点：」「主办单位：」「主讲人：」等标签抓同行内容，
  同行无内容则顺延到下一非空行，遇到下一个标签即截断。标签匹配要求处于行首/空白/标点之后，
  并把「发布/更新/浏览/截止/报名」作为前缀黑名单 —— 否则「**发布**时间：」会被当成「**时间**：」，
  把发布日期当成讲座时间。地点类标签不含「会议室」（它几乎总是出现在地点值内部，
  当标签会把「文泉楼北603会议室」截成「文泉楼北603」）。
- **经济学判定**（`classify.py`）：加权关键词命中 —— STRONG 词 3 分（经济学/财政/金融/收入分配/
  全要素生产率…）、MEDIUM 词 2 分（市场/贸易/投资/计量/劳动力…）、WEAK 词 1 分（政策/实证/均衡…）；
  标题命中额外加权 1.5 倍，经济学类学院再加 `econ_bias×2`。总分 ≥ `econ_threshold`（默认 3.0）
  即判为经济学相关。每条结论都会在报告里给出「判定依据」的命中词与得分，便于你调阈值。

### 8.5 去重与多用户隔离

- `articles` 表**全局共享**：同一条讲座只抓一次、只抽一次字段，多用户之间复用（`get_payloads`）。
- `user_articles` 表按用户记录「是否已收过」，实现**按用户维度的去重**。
- `user_state` 表记每位用户的 `last_push_date`，是「间隔 N 天」判定的依据。
- 推送失败时**不**推进 `last_push_date`，下次会重试，不会漏消息。

---

## 九、故障排查

| 现象 | 原因与处理 |
| --- | --- |
| 所有源都「抓取失败」，报告里 0 条 | 多半是浏览器通道不通。跑 `run.py doctor` 看浏览器通道；确认 Google Chrome 已安装且未被策略限制 |
| 个别源失败、其余正常 | 该学院改版了栏目结构。跑 `python scripts/discover_sources.py` 重新探测，更新 `sources.yaml` |
| 提示「检测到用户自己的 Chrome 正在运行」 | 执行时关掉 Chrome，或把定时时刻挪到你不用浏览器的时段 |
| 报告里「时间/地点」多是「见原文」 | 该篇正文里没有规范化标签（可能信息在图片里）。属正常，点原文链接即可 |
| 经济学判定不准 | 调 `econ_threshold`（调大更严），或改 `classify.py` 里的词表；报告会显示命中词方便定位 |
| 推送失败 | `run.py logs` 看具体返回；检查 key/token 是否有效、是否已绑定微信 |

---

## 十、已知限制

1. **公众号直连不可行**（见第二节）。默认走官网；要公众号原文需自建 RSSHub / wechat2rss。
2. **浏览器通道串行**，约 2 秒/页。一次全量抓取（19 源 × 2 页 + 部分正文）约需 2～4 分钟；
   若 20:00 那一刻各渠道确实没有新内容，整轮约 1 分钟。想更快可减少 `max_pages` 与 `detail_limit`。
3. **「地点/主讲人」覆盖率仍会偏低**：这些信息多数只出现在海报里，OCR 能读出一部分，
   但海报排版花哨时可能识别不全或漏行。报告会把读到的填上、读不到的标为「见原文」。
   **这是刻意的诚实行为**——不会用「发布时间」冒充讲座时间。
4. **OCR 只在本机 macOS 上生效**。非 macOS 环境会静默降级（时间读不出来的条目按
   `unknown_date_policy` 处理），不会报错中断。
5. **首次运行会回看 `lookback_days` 天**，条目可能较多；之后每次只推新增内容。
6. **「没有即将开始的讲座」是正常结果**。实测抓取时刻各渠道最新条目为 09-28，
   而当天是 09-30 —— 此时系统如实报「本期没有找到即将开始的讲座」，并列出 19 个渠道的状态。
   讲座通常在开讲前 1～2 周才发布，两次推送之间为空完全正常。
7. 各学院讲座栏目命名不统一（学术讲座 / 学术活动 / 讲座信息 / 学术交流…），
   已逐一实测确认。**有效栏目也可能暂时没有文章**（如金融学院·学术交流、
   文澜学院·学术活动、外国语学院·学术交流当前就是空栏目），这属于正常状态而非抓取失败。

---

## 十一、本项目的实测验证情况

以下结论均来自本机实际运行，不是推测：

| 验证项 | 结果 |
| --- | --- |
| 学院官网可达性与栏目探测 | 20 个学院全部实测，定位到 20 个栏目，当前 19 个启用 |
| WAF 挑战绕过 | `requests` 只拿到约 46KB 空壳页（0 链接）；无头 Chrome 渲染后可正常解析 |
| 渲染性能 | 约 1.7～2.0 秒/页 |
| 列表页解析 | 19 个渠道合计解析出 **406 条**历史条目（修掉了「图片链接先占位导致整页 0 条」的 bug） |
| 海报 OCR | 真实海报读出「讲座时间：2026年9月28日（周一）14:00-15:30」「讲座地点：文泉楼北603会议室」；首次约 1 秒/张，缓存后 0.09 秒 |
| 只留未开始的讲座 | 以 2026-09-20 为「今天」回溯：22 条候选里剔除 10 条已结束，保留 7 条（3 个渠道有更新） |
| 空结果分支 | 以 2026-09-30（真实日期）运行：全部条目的讲座日期都早于今天，如实报「本期没有找到即将开始的讲座」 |
| 报告结构 | 开头一句话 + 渠道清单；末尾 19 行全渠道状态（已更新 / 未更新讲座信息 / 有内容但均已结束 / 抓取失败） |
| 字段抽取 | 能抽到讲座时间的条目占比明显提升（OCR 补齐了纯海报条目） |
| 去重幂等 | 同一用户立即重跑，识别为「本期无新增」并跳过推送，不重复打扰 |
| 间隔调度 | `due` 正确跳过未满间隔的用户；`users` 显示的下次推送日期与配置一致 |
| 推送失败处理 | 用占位 key 实测 serverchan 返回「错误的Key」，此时**不推进** `last_push_date`，下次重试 |
| 并发保护 | 进程级锁，两个触发器同时跑不会重复推送 |

