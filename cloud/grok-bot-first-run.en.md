# First-run checklist (Grok Bot)

Grok Bot's docs leave a few things open, so check them one by one the first time and send the skill text only when all pass. The first cloud run on 2026-11-16 established these facts, and the checklist is written around them:

- scripts can run on the page (DevTools console; the first paste asks for `allow pasting`, which you type yourself);
- **a task is stopped after 50 minutes, and one command blocks for at most 13**;
- **between tasks the cloud computer sleeps and page timers stop**, while the page itself is not reloaded;
- all Bots share the cloud browser, which may be signed in to another account;
- with `write_to_file` on, tool results are saved as files named by the SHA-256 of their bytes.

Send the Bot the text of each step and note the result. Below, `xfa` means `python3 /workspace/xfa/app/xfa.py` and `<your handle>` is your X handle without @.

| # | What | What to tell the Bot | Pass | If not |
|---|---|---|---|---|
| 1 | The code | "On the cloud computer, run: `git clone https://github.com/LaughingisLaughing/x-following-audit.git /workspace/xfa/app && git -C /workspace/xfa/app checkout v0.5.0 && python3 --version && python3 /workspace/xfa/app/xfa.py --version`" | Python 3.8+ and `x-following-audit 0.5.0` | Python too old: ask the Bot to install 3.11 |
| 2 | `/workspace` persists | "Write a file `/workspace/xfa/persist-test.txt` holding the current time." After that task ends, in the next one: "Read `/workspace/xfa/persist-test.txt`." | The same time comes back | Do not set up daily runs |
| 3 | Where the data comes from | Quick check: "Call `get_users_me` with `user.fields=public_metrics` and `write_to_file` on; tell me the file path and my following count." Full check: "I'll give you a twitterapi.io key in a secure secret card. Hand it to the script without it entering chat or command history, then run `xfa rank <your handle> --estimate`." | Quick: a file path and the count; full: a credit estimate | Quick: check that the X plugin is connected; full: try another verified way to pass the key |
| 4 | Signed in to X | "Open x.com/home in the cloud browser. If it is not signed in, or not to my personal account, hand me the screen." You sign in or switch yourself | The home page shows your personal account | Follow any verification prompt; stop if you can't sign in |
| 5 | A read-only script runs on the page | "Run `xfa probe <your handle>`, run the script it prints in the x.com tab's console and show me the result. If it asks for `allow pasting`, stop and hand me the screen." | `profileLink` is your profile, `switcherHandles` has your handle, `twidReadable` is true | Without it nothing can be unfollowed automatically: stop here |
| 6 | Page timers between tasks | "Run `xfa probe <your handle> --timer` and run the printed script in the x.com tab." End the task; after 15 and 90 minutes ask: "Run `xfa probe <your handle> --timer-check`, run the script it prints and give me the result." | Read `kept_running` and `beats_missing`, not `age_s` alone (the new task wakes the computer, so the last beat is always recent). Found 2026-11: they stop between tasks | Stopping is normal: every batch runs inside one task (step 7) |
| 7 | How long one task lasts | "In one task, run `sleep 600` five times in a row with `date` after each, without ending the task. If the system cuts it off, tell me when and what it said." | At least 40 minutes. Found 2026-11: one command up to 13 minutes, one task up to 50 | Under 40 minutes: try small batches first with `batch ... --max 3` |
| 8 | A dry run | After the analysis (quick or full): "Run `date -u +%Y-%m-%dT%H:%M:%SZ` and note the task start, run `xfa plan <your handle> --tiers T2` and show me the plan, then run `xfa batch <your handle> P001 --max 3 --dry-run`, run the generated script on the x.com page, wait for it inside this task, run `xfaResult()`, save it and run `xfa sync <your handle> --file ...`." | Only `would_unfollow`, `already_not_following` and `mutual_now`; nothing unfollowed | Look at `note` for the reason and try again |
| 9 | Unfollow 3 for real | After you confirm the plan: "Run `xfa approve <your handle> P001 --sha <checksum>`, note the task start, run `xfa batch <your handle> P001 --max 3 --task-start <time>`, run it, wait inside this task, sync." Then open those 3 profiles on x.com and check the button says Follow | All 3 no longer followed | Stop and look before any routine |
| 10 | The routine | Create it as in `grok-bot-routine.en.md`. On the first Test, run only `xfa status <your handle> P001 --json`, no real batch | It fires on time and can be disabled | Disable it by hand at the end |

When all pass, send the text in `grok-bot-skill.en.md` to the Bot to save as a skill.

Notes:

- Test really runs. Never press Test on a real unfollow plan.
- All Bots share one cloud computer: the X session in its browser, its files and its command-line credentials are open to every Bot. Check the account before every batch.
- When you are done, ask the Bot to sign out of X; if you used a twitterapi.io key, revoke it in the dashboard.
