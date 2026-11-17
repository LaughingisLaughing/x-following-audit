# 首次上云验收清单（Grok Bot）

Grok Bot 的文档没写清几件事，第一次要逐项验证，全部通过再发 skill 说明。2026-11-16 的首次验收已经测出这些事实，清单按它们写：

- 页面里能执行脚本（开发者工具控制台；第一次粘贴要输入 `allow pasting`，由你本人输）；
- **一轮任务最长 50 分钟，单条命令最多阻塞 13 分钟**；
- **任务之间云电脑休眠，页面计时器停**，页面本身不会刷新；
- 云浏览器所有 Bot 共用，可能登录着别的账号；
- 工具返回打开 `write_to_file` 后存成文件，文件名是内容的 SHA-256。

每一步都把下面的话发给 Bot，并记下结果。下文 `xfa` 指 `python3 /workspace/xfa/app/xfa.py`，`<你的账号>` 换成你的 X 账号（不带 @）。

| # | 验证什么 | 发给 Bot 的话 | 通过的标准 | 不通过怎么办 |
|---|---|---|---|---|
| 1 | 拿到代码 | 「在云电脑上运行：`git clone https://github.com/LaughingisLaughing/x-following-audit.git /workspace/xfa/app && git -C /workspace/xfa/app checkout v0.6.0 && python3 --version && python3 /workspace/xfa/app/xfa.py --version`」 | 打印 Python 3.8 以上和 `x-following-audit 0.6.0` | Python 太旧，就请 Bot 装 3.11 |
| 2 | `/workspace` 能保留 | 「写一个文件 `/workspace/xfa/persist-test.txt`，内容是现在的时间。」等这个任务结束，下一次再问：「读一下 `/workspace/xfa/persist-test.txt`。」 | 读得到同一个时间 | 不启用续跑 |
| 3 | 数据从哪来 | 快速检查：「用自带的 `get_users_by_usernames` 查我自己的用户名，`user.fields` 带 `public_metrics`，打开 `write_to_file`，告诉我文件路径、关注数和返回末尾的剩余额度。」（关注名单之后在云浏览器里读，不用 X 插件）完整检查：「我会在安全密钥卡片里给你 twitterapi.io 的 key，用不会进聊天和命令记录的方式交给脚本，然后运行 `xfa rank <你的账号> --estimate`。」 | 快速检查拿到文件路径和关注数；完整检查打印出估算积分 | 快速检查拿不到，看返回里的额度说明，用完了等 UTC 0 点；完整检查换一种已验证的交 key 方式 |
| 4 | 登录 X | 「打开云浏览器的 x.com/home。没登录、或者登录的不是我的个人号，就把屏幕交给我。」你自己登录或切换到个人账号 | 首页显示你的个人账号 | 有验证就照提示做；登录不了就停 |
| 5 | 页面里能执行只读脚本 | 「运行 `xfa probe <你的账号>`，把它打印的脚本在 x.com 标签页的控制台里执行，把返回结果给我看。要求输入 `allow pasting` 时停下来，把屏幕交给我。」 | `profileLink` 是你的主页，`switcherHandles` 里有你的账号，`twidReadable` 是 true | 执行不了就不能自动取关，停在这里 |
| 6 | 计时器在任务之间还跑不跑 | 「运行 `xfa probe <你的账号> --timer`，把打印的脚本在 x.com 标签页执行。」结束任务，过 15 分钟、90 分钟后各问一次：「运行 `xfa probe <你的账号> --timer-check`，执行它打印的脚本，把结果给我。」 | 看 `kept_running` 和 `beats_missing`，不要只看 `age_s`（新任务会把云电脑叫醒，最后一跳总是很近）。2026-11 实测：任务之间会停 | 会停是常态：每批都在一轮任务里跑完（第 7 步） |
| 7 | 一轮任务能跑多久 | 「在同一个任务里，连续执行 5 次 `sleep 600`，每次结束后运行 `date`，中间不要结束任务。被系统中断就告诉我时间和提示。」 | 至少撑过 40 分钟。2026-11 实测：单条命令最多 13 分钟，一轮最多 50 分钟 | 撑不到 40 分钟，就用 `batch ... --max 3` 先试小批 |
| 8 | 试运行取关 | 走完 skill 的分析步骤（快速检查或完整检查）后：「运行 `date -u +%Y-%m-%dT%H:%M:%SZ` 记下任务开始时间，运行 `xfa plan <你的账号> --tiers T2` 生成计划给我看，然后运行 `xfa batch <你的账号> P001 --max 3 --dry-run`，在 x.com 页面执行生成的脚本，在这一轮里等它跑完，执行 `xfaResult()`，存文件，再运行 `xfa sync <你的账号> --file ...`。」 | 结果只有 `would_unfollow`、`already_not_following`、`mutual_now`，没有真的取关 | 看 `note` 里的原因，排查后再来 |
| 9 | 真取关 3 个 | 你确认计划后：「运行 `xfa approve <你的账号> P001 --sha <校验值>`，记下任务开始时间，运行 `xfa batch <你的账号> P001 --max 3 --task-start <时间>`。执行、在这一轮里等结束、sync。」再到 x.com 逐个打开这 3 个账号的主页，确认按钮是「关注」 | 3 个都不再关注 | 停下检查，先别开 routine |
| 10 | routine | 按 `grok-bot-routine.zh-CN.md` 建 routine。第一次用 Test 时，只运行 `xfa status <你的账号> P001 --json`，不要真发批次 | 能按时触发，也能停用 | 收尾时手动停用 |

全部通过后，把 `grok-bot-skill.zh-CN.md` 里的说明发给 Bot 存成 skill。

注意：

- Test 会真的执行，不要在真实的取关计划上点 Test。
- 所有 Bot 共用一台云电脑：浏览器里的 X 登录状态、文件、命令行凭证，任何一个 Bot 都能用。发批次前都要先核对账号。
- 用完了，就请 Bot 退出 X 的登录；用过 twitterapi.io key 的，在后台撤销这把 key。
