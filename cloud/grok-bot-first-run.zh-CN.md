# 首次上云验收清单（Grok Bot）

Grok Bot 的文档没写清几件事：

- 页面里能不能执行脚本；
- 任务结束后计时器还跑不跑；
- 密钥怎么交给脚本。

所以第一次先一项项验证，全部通过再发 skill 说明。每一步都把下面的话发给 Bot，并记下结果。

下文 `xfa` 指 `python3 /workspace/xfa/app/xfa.py`，`<你的账号>` 换成你的 X 账号（不带 @）。

| # | 验证什么 | 发给 Bot 的话 | 通过的标准 | 不通过怎么办 |
|---|---|---|---|---|
| 1 | 拿到代码 | 「在云电脑上运行：`git clone https://github.com/LaughingisLaughing/x-following-audit.git /workspace/xfa/app && git -C /workspace/xfa/app checkout v0.4.0 && python3 --version && python3 /workspace/xfa/app/xfa.py --version`」 | 打印 Python 3.8 以上和 `x-following-audit 0.4.0` | Python 太旧，就请 Bot 装 3.11 |
| 2 | `/workspace` 能保留 | 「写一个文件 `/workspace/xfa/persist-test.txt`，内容是现在的时间。」等这个任务结束，下一次再问：「读一下 `/workspace/xfa/persist-test.txt`。」 | 读得到同一个时间 | 不启用续跑 |
| 3 | key 交给脚本 | 「我会在安全密钥卡片里给你 twitterapi.io 的 key。请用不会进聊天和命令记录的方式交给脚本，然后运行 `xfa rank <你的账号> --estimate`。」 | 打印出你的关注数和估算积分 | 换另一种已验证的方式；还不行就停在这里 |
| 4 | 登录 X | 「打开云浏览器的 x.com/home。没登录的话把屏幕交给我。」你自己登录个人账号 | 首页显示你的账号 | 有验证就照提示做；登录不了就停 |
| 5 | 页面里能执行只读脚本 | 「运行 `xfa probe <你的账号>`，把它打印的脚本在 x.com 标签页里执行，把返回结果给我看。」 | `profileLink` 是你的主页，`switcherHandles` 里有你的账号；`twidReadable` 是 true 最好 | 执行不了就不能自动取关，停在这里 |
| 6 | 计时器在任务之间还跑不跑 | 「运行 `xfa probe <你的账号> --timer`，把打印的脚本在 x.com 标签页执行。」然后结束任务，过 15 分钟、90 分钟后各问一次：「运行 `xfa probe <你的账号> --timer-check`，执行它打印的脚本，把结果给我。」 | `age_s` 小于 60，说明一直在跑 | 计时器会停：每批在一次任务里跑完（单次任务要能跑 55 分钟） |
| 7 | 单次任务能跑多久 | 「执行一个只等待、不做事的命令：`sleep 3300; echo done`。」 | 55 分钟后打印 done | 时长不够，就把批次改小（`batch ... --max 3`） |
| 8 | 试运行取关 | 走完 skill 的分析步骤后，告诉 Bot：「运行 `xfa plan <你的账号> --tiers T2` 生成计划给我看，然后运行 `xfa batch <你的账号> P001 --max 3 --dry-run`，在 x.com 页面执行生成的脚本。跑完后执行 `xfaResult()`，存文件，再运行 `xfa sync <你的账号> --file ...`。」 | 结果只有 `would_unfollow`、`already_not_following`、`mutual_now`，没有真的取关 | 看 `note` 里的原因，排查后再来 |
| 9 | 真取关 3 个 | 你确认计划后，告诉 Bot：「运行 `xfa approve <你的账号> P001 --sha <校验值>`，然后运行 `xfa batch <你的账号> P001 --max 3`。执行、等结束、sync，再运行 `xfa verify <你的账号> P001`。」 | 3 个都核实不再关注 | 停下检查，先别开 routine |
| 10 | routine | 按 `grok-bot-routine.zh-CN.md` 建 routine。第一次用 Test 时，只运行 `xfa status <你的账号> P001 --json`，不要真发批次 | 能按时触发，也能停用 | 收尾时手动停用 |

全部通过后，把 `grok-bot-skill.zh-CN.md` 里的说明发给 Bot 存成 skill。

注意：

- Test 会真的执行，不要在真实的取关计划上点 Test。
- 所有 Bot 共用一台云电脑：浏览器里的 X 登录状态、文件、命令行凭证，任何一个 Bot 都能用。别的 Bot 不要碰 X。
- 用完了，就请 Bot 退出 X 的登录，并在 twitterapi.io 后台撤销这把 key。
