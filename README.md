# x-following-audit

<p align="center">English | <a href="README.zh-CN.md">简体中文</a></p>

Find the accounts in your X (Twitter) following list that are worth unfollowing: no posts in the last 6 months, fewer than 5 posts in the last year, or (optional) almost never replying to or mentioning you.

It is a single Python script with no dependencies. It runs on your own machine with your own [twitterapi.io](https://twitterapi.io/?ref=Laughing) API key, and it never unfollows anyone for you.

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

- Unfollow slowly: at most 20 accounts per hour and 100 per day, with breaks in between, or X may flag your account.
- Your API key is only sent to api.twitterapi.io. Nothing else leaves your machine.
- Protected accounts can't be checked; they are listed separately.
- If you follow many people, `--interaction` flags most of them, because most accounts never reply to you. It is most useful as the extra 和你互动 (interactions with you) column in the inactive list.
- `--engine grok` and `grok_bridge.py` are for people who run [Hermes Agent](https://github.com/NousResearch/hermes-agent) signed in with SuperGrok. Everyone else can ignore them.

## License

[MIT](LICENSE)
