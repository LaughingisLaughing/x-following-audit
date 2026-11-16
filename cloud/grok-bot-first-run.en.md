# First-run checklist for Grok Bot

Grok Bot's documentation leaves a few things open:

- whether a page can run a script;
- whether page timers keep running after a task ends;
- how a secret reaches a script.

So check them one by one the first time. Send the Bot the words below for each step and note the result; only after every check passes, send it the skill text.

Below, `xfa` means `python3 /workspace/xfa/app/xfa.py`, and `<your handle>` is your X handle without @.

| # | What | What to tell the Bot | Pass | If it fails |
|---|---|---|---|---|
| 1 | Get the code | "On your cloud computer run: `git clone https://github.com/LaughingisLaughing/x-following-audit.git /workspace/xfa/app && git -C /workspace/xfa/app checkout v0.4.0 && python3 --version && python3 /workspace/xfa/app/xfa.py --version`" | Python 3.8+ and `x-following-audit 0.4.0` | Old Python: ask the Bot to install 3.11 |
| 2 | `/workspace` persists | "Write the current time into `/workspace/xfa/persist-test.txt`." After that task ends, in a new one: "Read `/workspace/xfa/persist-test.txt`." | Same time comes back | Do not use the routine |
| 3 | Key reaches the script | "I will give you my twitterapi.io key in the secure secret card. Hand it to the script in a way that stays out of chat and command history, then run `xfa rank <your handle> --estimate`." | Prints your following count and the estimate | Try another verified way; if none works, stop here |
| 4 | Sign in to X | "Open x.com/home in the cloud browser. If it is not signed in, hand me the screen." You sign in with your personal account | Home shows your account | Follow X's verification; if you cannot sign in, stop |
| 5 | A read-only page script runs | "Run `xfa probe <your handle>`, run the script it prints in the x.com tab, and show me what it returns." | `profileLink` is your profile, `switcherHandles` contains your handle; `twidReadable` true is best | No script execution means no automatic unfollowing: stop here |
| 6 | Timers between tasks | "Run `xfa probe <your handle> --timer` and run the printed script in the x.com tab." End the task; after 15 and after 90 minutes ask: "Run `xfa probe <your handle> --timer-check`, run the printed script, show me the result." | `age_s` below 60: it kept running | Timers stop: let each batch finish within one task (a task must last 55 minutes) |
| 7 | How long one task can run | "Run a command that only waits: `sleep 3300; echo done`." | Prints done after 55 minutes | Too short: use smaller batches (`batch ... --max 3`) |
| 8 | Dry run | After the analysis steps of the skill: "Run `xfa plan <your handle> --tiers T2` and show me the plan, then run `xfa batch <your handle> P001 --max 3 --dry-run`, run the generated script on x.com, and when it ends run `xfaResult()`, save it and run `xfa sync <your handle> --file ...`." | Only `would_unfollow`, `already_not_following` or `mutual_now`; nothing unfollowed | Read the `note` fields, fix, retry |
| 9 | Unfollow 3 for real | After you confirm the plan: "Run `xfa approve <your handle> P001 --sha <checksum>`, then `xfa batch <your handle> P001 --max 3`, run it, wait for the end, sync, then `xfa verify <your handle> P001`." | All 3 verified as no longer followed | Stop and look before any routine |
| 10 | Routine | Create the routine from `grok-bot-routine.en.md`; the first Test only runs `xfa status <your handle> P001 --json`, never a real batch | Fires on time and can be turned off | Turn it off by hand at the end |

When all pass, send the Bot the text in `grok-bot-skill.en.md` to save as a skill.

Mind:

- Test runs do real work. Never press Test on a real unfollow plan.
- All your Bots share one cloud computer: the X session in the browser, the files and the command-line credentials are usable by every Bot. Keep other Bots away from X.
- When you are done, ask the Bot to sign out of X, and revoke the key in the twitterapi.io dashboard.
