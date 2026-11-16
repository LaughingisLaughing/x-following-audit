# x-following-audit

<p align="center">English | <a href="README.zh-CN.md">简体中文</a></p>

Find the accounts in your X (Twitter) following list that are worth unfollowing: no posts in the last 6 months, fewer than 5 posts in the last year, or (optional) almost never replying to or mentioning you.

It is plain Python with no dependencies, run on your own machine (or an agent's cloud computer) with your own [twitterapi.io](https://twitterapi.io/?ref=Laughing) API key. It comes in two parts:

- `audit` finds inactive and low-activity accounts. It never unfollows anyone.
- The tiered cleanup (new in 0.4.0) sorts everyone you follow into tiers, lets you pick which tiers to drop, and then unfollows only the list you confirmed, slowly, in your own signed-in browser. See [Tiered cleanup](#tiered-cleanup-040).

This is the successor to the retired web app "X Follower Tool" (twitter-unfollow.up.railway.app). The rules are the same.

## What you need

- Python 3.8 or newer. Check with `python3 --version`.
- A twitterapi.io API key. New accounts get up to 1 USD of free credits (100,000 credits). Exporting a following list costs about 1 credit per account; checking one account's recent posts costs about 300 credits.

## Quick start

Download the script:

```bash
curl -O https://raw.githubusercontent.com/LaughingisLaughing/x-following-audit/main/xfa.py
```

Then choose how the accounts get checked. Add `--lang en` to either command to get the summary and the Grok prompts in English.

**Option 1: let Grok check them (almost free).** The script only exports your following list and writes ready-to-paste prompts, 100 accounts per part:

```bash
python3 xfa.py audit your_handle --engine none
```

Open the generated `~/Downloads/x-following-audit/your_handle-YYYYMMDD-grok-prompts.txt`, paste one part at a time into [grok.com](https://grok.com), and Grok lists the accounts with no posts in the last 6 months.

**Option 2: full report.** Every account is checked through the API, about 300 credits each:

```bash
python3 xfa.py audit your_handle
```

The script asks for your API key and hides what you type. To skip the prompt, run `export TWITTERAPI_IO_KEY=your_key` first.

## Tiered cleanup (0.4.0)

Get all files (the tiered cleanup needs `xfa_tiers.py` and `runner.js` next to `xfa.py`):

```bash
git clone https://github.com/LaughingisLaughing/x-following-audit.git && cd x-following-audit
```

1. Estimate the cost, then collect. The first run stops and asks at most three multiple-choice questions (languages you read, what you follow people for, accounts to protect such as your company's):

   ```bash
   python3 xfa.py rank your_handle --estimate
   python3 xfa.py rank your_handle --max-credits 1200000
   python3 xfa.py profile your_handle --languages en --topics "AI;design" --confirm
   python3 xfa.py rank your_handle --max-credits 1200000
   ```

2. Optional content review: `rank` writes batches of 25 accounts (`runs/<date>/judge/bNNN.md`). Any LLM agent can read a batch and write the answers to `bNNN.jsonl`; `python3 xfa.py judge your_handle --import` checks them strictly. With a local [Hermes Agent](https://github.com/NousResearch/hermes-agent) signed in to SuperGrok: `python3 xfa.py judge your_handle --hermes --batches 1`.
3. `python3 xfa.py tiers your_handle` writes a report with these tiers:

   | Tier | What is in it |
   |---|---|
   | Tier 1: safe to unfollow | the rules and the review both say unfollow |
   | Tier 2: suggested | proven by data: no post in 6 months, fewer than 5 posts a year, promotional posts; or floods / only replies / only reposts and the review agrees |
   | Tier 3: your call | matters of taste: off your topics, low signal, languages you don't read; off by default |
   | Mutuals | accounts that follow you back: never picked as a whole tier, only one by one |
   | Keep / not enough evidence | never unfollowed |

   Account size, likes and whether they follow you back are not scored. Full rules (Chinese): [TIERS.zh-CN.md](TIERS.zh-CN.md).
4. Freeze your choice into a plan and approve it with the checksum it prints:

   ```bash
   python3 xfa.py plan your_handle --tiers T1,T2
   python3 xfa.py approve your_handle P001 --sha <checksum>
   ```

5. Unfollow in batches of at most 15. `batch` writes a script to run on an x.com tab signed in as you (DevTools console or a browser automation tool); afterwards `xfaResult()` returns the result for `sync`:

   ```bash
   python3 xfa.py batch your_handle P001
   python3 xfa.py sync your_handle --file result.json
   python3 xfa.py status your_handle P001
   ```

   The pace is enforced: at most 18 an hour, 100 in 24 hours, 150-210 seconds apart. Before every click the script checks that X is acting as your account, and it stops on any mismatch, login prompt or rate-limit notice.

### Use it in Grok Bot

The whole flow can run on a [Grok Bot](https://docs.x.ai/grok-bot/overview) cloud computer, with a routine continuing the batches every two hours. Start with [cloud/grok-bot-first-run.en.md](cloud/grok-bot-first-run.en.md), then send the Bot [cloud/grok-bot-skill.en.md](cloud/grok-bot-skill.en.md) and [cloud/grok-bot-routine.en.md](cloud/grok-bot-routine.en.md).

## Output

Files go to `~/Downloads/x-following-audit/`:

- `your_handle-YYYYMMDD.md`: unfollow candidates grouped by reason, each with a profile link (the report text is in Chinese)
- `your_handle-YYYYMMDD.csv`: every account you follow, with its last post date, posts in the window, and why it was flagged
- `your_handle-YYYYMMDD-grok-prompts.txt`: only with `--engine none`

## Options

| Option | What it does |
|---|---|
| `--engine none` | Export the list and the Grok prompts only |
| `--interaction` | Also flag accounts that replied to or mentioned you fewer than 3 times in the last year |
| `--inactive-months 6` `--low-tweets 5` `--low-days 365` | Thresholds, same defaults as the web app |
| `--skip-heavy 5000` | Treat accounts with 5,000+ total posts as active without checking them. Cuts API calls roughly in half, but in testing it missed about a quarter of the unfollow candidates |
| `--limit 50` | Only check the 50 most recently followed accounts, for a trial run |
| `--lang en` | English summary and Grok prompts |
| `--out DIR` | Write the files somewhere else |

Run `python3 xfa.py audit --help` for the full list.

## Good to know

- Unfollow slowly: at most 18 accounts per hour and 100 per day, with breaks in between, or X may flag your account. The tiered cleanup enforces this for you.
- Your API key is only sent to api.twitterapi.io. Nothing else leaves your machine.
- Protected accounts can't be checked; they are listed separately.
- twitterapi.io's following list sometimes holds more accounts than the following count X shows. The extra ones are accounts you no longer follow, and they can end up among the candidates. The script points this out in the summary and at the top of the report. When you unfollow, skip any account whose button says Follow instead of Following.
- If you follow many people, `--interaction` flags most of them, because most accounts never reply to you. It is most useful as the extra 和你互动 (interactions with you) column in the inactive list.
- `--engine grok` and `grok_bridge.py` are for people who run [Hermes Agent](https://github.com/NousResearch/hermes-agent) signed in with SuperGrok. Everyone else can ignore them.

## License

[MIT](LICENSE)
