# Routine for Grok Bot (English)

Create it after the plan is approved and the first batch went well. Replace `<your handle>` with your X handle, `P00X` with the real plan id and the times with the hours you like, then send the whole block below to the Bot.

---

Please create a routine named "XFA unfollow P00X" at 08:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00 and 22:00 every day. Each run uses the skill "X following cleanup by tiers" and handles only plan P00X of `<your handle>`. Each run is one batch, started, waited for and synced inside the run:

1. Run `date -u +%Y-%m-%dT%H:%M:%SZ`; that is this run's start time.
2. Run `xfa status <your handle> P00X --json`. If `caps.unsynced` lists a batch left by an earlier run, **close it first**: open a new x.com tab, run the same `batches/BNNN.js` again (it only closes the batch safely and hands over the result), then `xfaResult()`; when `status` is `done` or `stopped: ...`, save it as `batches/BNNN.result.json` and run `xfa sync`. If it cannot be recovered and expired more than 5 minutes ago, run `xfa batch <your handle> P00X --abandon BNNN`. Then **run** `xfa status` **again** and go by the new result.
3. If `complete` is true, or `waiting_on_user` is true, or the plan expired or was revoked:
   - for a full check run `xfa verify <your handle> P00X` and look at `status` again; a quick-check plan cannot be verified, so skip it;
   - only if one of these still holds, send me the final result and disable this routine; if accounts can go on, carry on.
4. If `caps.next_eligible` is still ahead, issue no batch this run, and finish.
5. When a batch can go:
   - open `https://x.com/home` and run the read-only probe from `xfa probe <your handle>`; continue only if all three account checks pass;
   - run `xfa batch <your handle> P00X --task-start <the time from step 1>`, run the generated `BNNN.js` on that page, and after a few seconds read `window.__xfaStartMessage` to confirm it started;
   - wait at most 10 minutes at a time, then run `xfaResult()` on the page: on `done` or `stopped: ...` save the file and run `xfa sync`; if it is still `running` at the batch's deadline, run `xfaStop()`, wait for `stopped`, then save and sync. Finish the whole run within 45 minutes.
6. Do not issue a batch when:
   - a sign-in or verification is needed, or the account is wrong;
   - Chrome asks for `allow pasting` (I type that myself);
   - a rate-limit notice appears, or the confirmation sheet does not match the target;
   - the Grok usage or the X read quota is used up;
   - `sync` reported a ⚠️.

   Tell me why, disable this routine, and wait until I have dealt with it.

Reply with one or two lines only: how many were unfollowed today, how many are left, when the next batch runs. Do not repeat account details, do not collect the full data again, do not review again, never widen the list.

---

Notes:

- Every run uses your weekly Grok usage, even when it does nothing. Eight a day is plenty; do not make it hourly.
- The script sets the pace: at most 18 an hour and 100 in 24 hours; on the cloud computer at most 10 per batch and 80 planned a day.
- Why every batch finishes inside one run: a Grok Bot task is stopped after 50 minutes, and between tasks the cloud computer sleeps and the page script stops; when it wakes, the script ends that batch instead of clicking on.
