# x-following-audit

<p align="center"><a href="README.md">English</a> | 简体中文</p>

找出你在 X（Twitter）上关注的账号里值得取关的那些：半年没发帖、一年发不到 5 条，或者（可选）几乎从不回复、提到你。

它是纯 Python，不依赖任何第三方库，在你自己的电脑（或 agent 的云电脑）上，用你自己的 [twitterapi.io](https://twitterapi.io/?ref=Laughing) key 运行。分两部分：

- `audit`：找出不活跃、低活跃的账号，不会替你取关任何人。
- 分级清理（0.4.0 新增）：把你关注的人分成几级，你选要清理哪几级，然后只按你确认过的名单，在你自己已登录的浏览器里慢慢取关。见[分级清理](#分级清理040)。

它接替已经下线的网页版「X Follower Tool / 关注列表分析器」（twitter-unfollow.up.railway.app），判定规则不变。

## 需要准备

- Python 3.8 或更新版本，用 `python3 --version` 查看。
- 一个 twitterapi.io 的 API key。新注册账号送最多 1 美元免费额度（10 万积分）。导出关注名单每个账号约 1 积分；检查一个账号最近的帖子约 300 积分。

## 快速开始

下载脚本：

```bash
curl -O https://raw.githubusercontent.com/LaughingisLaughing/x-following-audit/main/xfa.py
```

然后选一种检查方式。两条命令都可以加 `--lang en`，屏幕摘要和 Grok 指令会改成英文。

**方式一：交给 Grok 查（几乎免费）**。脚本只导出你的关注名单，并按每 100 个一段写好可以直接粘贴的指令：

```bash
python3 xfa.py audit your_handle --engine none
```

打开生成的 `~/Downloads/x-following-audit/your_handle-YYYYMMDD-grok-prompts.txt`，一段一段贴进 [grok.com](https://grok.com)，Grok 会列出半年内没发过帖的账号。

**方式二：完整报告**。每个账号都通过接口检查，每个约 300 积分：

```bash
python3 xfa.py audit your_handle
```

脚本会提示输入 API key，输入的内容不会显示。不想每次输入，可以先运行 `export TWITTERAPI_IO_KEY=your_key`。

## 分级清理（0.4.0）

先拿到全部文件（分级清理需要 `xfa_tiers.py` 和 `runner.js` 跟 `xfa.py` 放在一起）：

```bash
git clone https://github.com/LaughingisLaughing/x-following-audit.git && cd x-following-audit
```

1. 先估算费用，再收集数据。第一次运行会停下来，问你最多三道选择题：你读得懂的语言、你关注别人主要为了什么、要保护哪些账号（比如你公司的号）：

   ```bash
   python3 xfa.py rank your_handle --estimate
   python3 xfa.py rank your_handle --max-credits 1200000
   python3 xfa.py profile your_handle --languages zh,en --topics "AI;设计" --confirm
   python3 xfa.py rank your_handle --max-credits 1200000
   ```

2. 内容审核（可选）：`rank` 会生成每批 25 个账号的判断任务（`runs/<日期>/judge/bNNN.md`）。任何一个大模型 agent 都可以读一批、把结果写进 `bNNN.jsonl`，再用 `python3 xfa.py judge your_handle --import` 严格校验后导入。本机装了 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 并登录 SuperGrok 的，可以用 `python3 xfa.py judge your_handle --hermes --batches 1`。
3. `python3 xfa.py tiers your_handle` 生成报告，分成这几级：

   | 级别 | 里面是什么 |
   |---|---|
   | 一级 放心取关 | 规则和审核都建议取关 |
   | 二级 建议取关 | 数据能证明的：半年没发帖、一年不到 5 条、营销帖；或者刷屏、几乎全是回复、几乎只转发，并且审核也同意 |
   | 三级 看你喜好 | 口味问题：和你的主题无关、信息量低、读不懂的语言；默认不选 |
   | 互关 | 也关注了你的账号：不能整级选，只能逐个挑 |
   | 留着、证据不足 | 永远不会被取关 |

   粉丝多少、点赞多少、有没有回关，都不计分。完整规则见 [TIERS.zh-CN.md](TIERS.zh-CN.md)。
4. 把你的选择冻结成计划，用它打印的校验值批准：

   ```bash
   python3 xfa.py plan your_handle --tiers T1,T2
   python3 xfa.py approve your_handle P001 --sha <校验值>
   ```

5. 分批取关，每批最多 15 个。`batch` 会生成一段脚本，在已登录你账号的 x.com 标签页里执行（DevTools 控制台，或浏览器自动化工具）；跑完后用 `xfaResult()` 拿到结果，交给 `sync`：

   ```bash
   python3 xfa.py batch your_handle P001
   python3 xfa.py sync your_handle --file result.json
   python3 xfa.py status your_handle P001
   ```

   节奏由程序强制：每小时最多 18 个，24 小时最多 100 个，两次间隔 150-210 秒。每次点击前，脚本都核对 X 当前是不是以你的账号在操作；账号对不上、要求登录、出现限制提示，都会立刻停下。

### 在 Grok Bot 里用

整个流程可以放在 [Grok Bot](https://docs.x.ai/grok-bot/overview) 的云电脑上跑，用 routine 每两小时续跑一批。先看 [cloud/grok-bot-first-run.zh-CN.md](cloud/grok-bot-first-run.zh-CN.md)，再把 [cloud/grok-bot-skill.zh-CN.md](cloud/grok-bot-skill.zh-CN.md) 和 [cloud/grok-bot-routine.zh-CN.md](cloud/grok-bot-routine.zh-CN.md) 发给 Bot。

## 输出

文件都写到 `~/Downloads/x-following-audit/`：

- `your_handle-YYYYMMDD.md`：按原因分组的取关候选，每个账号带主页链接
- `your_handle-YYYYMMDD.csv`：你关注的全部账号，含最后发帖日期、窗口内的帖子数，以及被标记的原因
- `your_handle-YYYYMMDD-grok-prompts.txt`：只有加 `--engine none` 时才会生成

## 参数

| 参数 | 作用 |
|---|---|
| `--engine none` | 只导出名单和 Grok 指令 |
| `--interaction` | 同时标出最近一年里回复加提到你少于 3 次的账号 |
| `--inactive-months 6` `--low-tweets 5` `--low-days 365` | 判定阈值，默认值和网页版一致 |
| `--skip-heavy 5000` | 总帖子数 5000 以上的账号直接视为活跃，不再检查。接口调用大约少一半，但实测漏掉了约四分之一的取关候选 |
| `--limit 50` | 只检查最近关注的 50 个账号，试跑用 |
| `--lang en` | 屏幕摘要和 Grok 指令用英文 |
| `--out DIR` | 把文件写到别的目录 |

完整参数见 `python3 xfa.py audit --help`。

## 注意事项

- 取关请放慢：每小时不超过 18 个，每天不超过 100 个，中间留出间隔，否则可能触发 X 的风控。分级清理会替你强制这个节奏。
- API key 只会发给 api.twitterapi.io，其他数据都不会离开你的电脑。
- 私密账号看不到帖子，只单独列出，不做判定。
- twitterapi.io 的关注名单有时比 X 显示的关注数多，多出来的是你已经不再关注的账号，会混在候选名单里。脚本发现名单多出来时，会在摘要和报告开头提示。取关时按钮显示「关注」而不是「正在关注」的，直接跳过。
- 关注的人很多时，`--interaction` 会标出其中的大部分，因为大多数账号本来就不会回复你。它最有用的地方，是不活跃列表里多出来的「和你互动」那一列。
- `--engine grok` 和 `grok_bridge.py` 是给本机装了 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 并登录 SuperGrok 的人用的，其他人可以忽略。

## 许可证

[MIT](LICENSE)
