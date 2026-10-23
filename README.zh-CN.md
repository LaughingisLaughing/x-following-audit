# x-following-audit

<p align="center"><a href="README.md">English</a> | 简体中文</p>

找出你在 X（Twitter）上关注的账号里值得取关的那些：半年没发帖、一年发不到 5 条，或者（可选）几乎从不回复、提到你。

它只是一个 Python 脚本，不依赖任何第三方库。在你自己的电脑上，用你自己的 [twitterapi.io](https://twitterapi.io/?ref=Laughing) key 运行，不会替你取关任何人。

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

- 取关请放慢：每小时不超过 20 个，每天不超过 100 个，中间留出间隔，否则可能触发 X 的风控。
- API key 只会发给 api.twitterapi.io，其他数据都不会离开你的电脑。
- 私密账号看不到帖子，只单独列出，不做判定。
- 关注的人很多时，`--interaction` 会标出其中的大部分，因为大多数账号本来就不会回复你。它最有用的地方，是不活跃列表里多出来的「和你互动」那一列。
- `--engine grok` 和 `grok_bridge.py` 是给本机装了 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 并登录 SuperGrok 的人用的，其他人可以忽略。

## 许可证

[MIT](LICENSE)
