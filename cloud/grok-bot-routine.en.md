# The continuation routine for Grok Bot (English)

Create it only after a plan is approved and the first batch went fine. Replace `<your handle>` with your X handle, `P00X` with the plan id, and the times with hours that suit you, then send the whole text below to the Bot.

---

Please create a routine named "XFA unfollow P00X" that runs every day at 08:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00 and 22:00. Each run uses the skill "X following cleanup by tiers", handles plan P00X of `<your handle>` only, and goes as follows:

1. Run `xfa status <your handle> P00X --json`.
2. If `complete` is true, or `waiting_on_user` is true, or the plan expired or was revoked:
   - run `xfa verify <your handle> P00X`, then `xfa status <your handle> P00X --json` again;
   - only if it is still one of these, send me the final result and turn this routine off; if the check freed accounts to continue, go on.
3. If `caps.unsynced` lists a batch, run `xfaResult()` in the x.com tab and look at `status`:
   - Still `running`: do nothing this time and wait for the next run.
   - `done` or `stopped: ...`: save the text as that batch's `batches/BNNN.result.json` and run `xfa sync <your handle> --file <file>`.
   - Page gone: open a new x.com tab and run the same `batches/BNNN.js` again. It does not rerun the batch; once it holds the runner lock it closes the batch safely and hands over the result. Then run `xfaResult()` and sync as above.
   - If it answers refused, the old page is still running: wait for the next run.
   - If the result is truly lost and the batch expired more than 5 minutes ago, run `xfa batch <your handle> P00X --abandon BNNN`, then `xfa verify <your handle> P00X`.
4. If `caps.next_eligible` has not come yet, issue no new batch.
5. When a batch can go:
   - run the read-only probe from `xfa probe <your handle>` on the x.com page first, and continue only if all three identity checks pass;
   - then run `xfa batch <your handle> P00X` and run the generated `BNNN.js` in that page;
   - after a few seconds, read `window.__xfaStartMessage` to confirm it started.
6. Issue no batch if any of these happens:
   - X asks to sign in or verify;
   - the account does not match;
   - a rate-limit notice shows, or the confirmation sheet does not match;
   - Grok usage ran out;
   - `sync` printed a ⚠️.

   Tell me why and turn this routine off until I have dealt with it.

Reply with one or two lines only: how many unfollowed today, how many left, when the next batch goes. Do not repeat account details, do not collect all data again, do not review again, do not widen the list.

---

Notes:

- Every run counts against Grok's weekly usage, even when it does nothing. Eight runs a day is enough; do not make it hourly.
- The script sets the pace: at most 18 an hour, 100 in 24 hours, 90 planned a day.
- If page timers stop between tasks (the first-run check tells you), let each batch finish within one task, at most 55 minutes.
