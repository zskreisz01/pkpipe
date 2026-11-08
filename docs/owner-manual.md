---
title: Owner manual
---

# Owner manual

This page lists only what **you**, the owner, have to do by hand. Everything else runs on its own. pkpipe never processes, releases or adopts anything without you (constitution VI). If you skip a task, nothing breaks: items wait in a pending or proposed state until you get to them.

> **Status:** pkpipe is pre-alpha (the config UI, `pkpipe serve` and the app MCP server exist; see [Operations](operations.md#config-ui-and-app-mcp)). This manual describes the designed behaviour (see `docs/` and `specs/`); file names and UI views may still change while the specs are implemented.

Times are the defaults from `schedules.json`. If you change a schedule, the matching task moves with it.

## At a glance

| When | Task | Where | If you skip it |
| --- | --- | --- | --- |
| Any time | [Capture resources](#capture-resources) | Obsidian `0-INBOX` | Nothing is processed |
| End of day, before 21:30 | [Write the daily journal](#write-the-daily-journal) *(overlay)* | Obsidian journal | No improvement items that day |
| After the 20:00 notification, before 06:00 | [Confirm or override triage](#confirm-or-override-triage) | `Inbox-Triage.md`, UI or chat agent | Items stay pending and come back in the next notification |
| After the 06:00 summary | [Work through the processing list](#work-through-the-processing-list) | `Manual-Resource-Processing-List.md` | The list grows; nothing is lost |
| After the 06:00 summary, if items were quarantined | [Review quarantine](#review-quarantine) | `0-INBOX/_quarantine/`, UI | False positives are never processed |
| Immediately, on a security alert | [React to a security alert](#react-to-a-security-alert) | Notification, trace backend | A real attack may go unnoticed |
| Sunday, after 19:00 | [Review context pack diff](#review-context-pack-diff) | `contexts/` in the overlay | Sessions use stale or wrong context |
| Sunday, after 19:30 | [Weekly improvement review](#weekly-improvement-review) *(overlay)* | Review note, UI | Verdicts stay `proposed` |
| Weekly review | [Pick system tool candidates](#pick-system-tool-candidates) | `System-Tool-Candidates.md` | Candidates stay listed |
| Weekly, when the report arrives | [Check the eval report](#check-the-eval-report) | Notification, trace backend | Slow quality drift goes unnoticed |
| On occasion | [Occasional tasks](#occasional-tasks) | UI, terminal | See each task |

```mermaid
flowchart LR
    subgraph Daily
        c[Capture any time] --> n20((20:00<br/>triage notification)) --> t[Confirm / override<br/>before 06:00] --> n06((06:00<br/>summary)) --> p[Processing list] --> q[Quarantine review]
        j[Journal before 21:30]
    end
    subgraph Sunday
        cx((19:00)) --> cr[Review context diff]
        ir((19:30)) --> iv[Decide verdicts] --> st[Pick tool candidates]
    end
```

## Daily

### Capture resources

- **What:** put anything you want handled into the INBOX: a link, a short text, an image, with an optional note on why it matters to you.
- **When:** any time. Items captured after 20:00 are picked up the next evening.
- **How:** create a note in `0-INBOX` (or, when your layout names an `inbox_list_file`, add a bullet to that list: a link, `![[image]]`, a title with its URL on the next line, or plain text; see [Configuration](configuration.md#the-inbox-list-file)) (Obsidian, mobile share sheet, web clipper). One resource per note. A line of your own context ("for client onboarding", "compare with X") improves the category suggestion a lot.

### Write the daily journal

*Only if the self-improvement cycle is enabled in your overlay.*

- **What:** a short daily note on what went well, what did not and what you want to try.
- **When:** before 21:30, when the extraction runs.
- **How:** write in the day's journal note (`2-Areas/Journal/YYYY-MM-DD.md` by default) and put what you want to change under an `## Improvement` heading, one bullet per item, in your own words ("Stretch for ten minutes every morning."). Add how you will know it worked in the same bullet if you like. The extraction session copies each bullet as an improvement item in state `proposed`; it never rewrites or invents. Only that section is read by the session, which runs locally without tools, and the journal never leaves the machine.

### Review the improvement backlog

*Only if the self-improvement cycle is enabled in your overlay.*

- **What:** decide what happens to the proposed items and to the weekly verdict proposals.
- **When:** after the Sunday 19:30 "weekly review ready" notification.
- **How:** open `2-Areas/Self-Improvement/Improvement-Backlog.md`. Set **status** yourself: `proposed` to `active` (or `dropped`), later `kept`, `adjusted` or `dropped`. The agent never changes a status; it only writes the **evidence** (quotes from your journal, each checked against the journal text), a **verdict** (keep / adjust / drop) with a **reason** and a **next** experiment for active items. A `kept` item of area `system` gets a proposal note in `2-Areas/Self-Improvement/System-Changes/`: applying it is a config change that only publishes through the eval gate.
- **If you skip it:** nothing changes; the next review makes a new proposal.

### Confirm or override triage

- **What:** check the AI's category suggestion for each new item.
- **When:** after the 20:00 "triage ready" notification, before 06:00 the next morning.
- **How**, one of:
  - **Web UI (primary, spec 023):** the triage page `/triage`: image thumbnails, every field, filters, **Confirm all suggested in this set**, a category list and *propose a new category*, and your comment. See the [User guide](user-guide.md#deciding-in-the-web-ui).
  - **Obsidian** (fallback on the `vault` surface; on the `web` surface the file is only a summary page with a link): open `0-INBOX/Inbox-Triage.md`. For each row, tick the **confirm** checkbox if the suggestion is right, or write a category id into **override**. Leave a row alone to keep it pending.
  - **Chat agent:** ask your MCP chat client to `list_triage`, then `confirm_item` / `override_item` (and `retry_items` for failed items, see the user guide; the CLI has `pkpipe retry`; `item_history` shows everything that happened to one item, the CLI has `pkpipe item`). These are owner actions: the client must use the owner token (the agent token can only list and run flows). Each decision is logged on the UI audit page.
- **Tips:** the notification shows confidence and reason per item; look closely at low-confidence ones. Every override becomes a training signal for the triage eval, so overriding is better than confirming a wrong category.
- **If you skip it:** unreviewed items stay `pending`, are counted in the 06:00 daily summary and appear again in the next triage notification ("N older item(s) still wait for your decision"). Nothing is processed by default.

### Work through the processing list

- **What:** consume what pkpipe prepared: listen to the podcast, read the notebook, read the tool evaluation or the curated note.
- **When:** after the 06:00 daily summary, whenever it suits you.
- **How:** open `Manual-Resource-Processing-List.md`. Each row links to the note, and for learning material to the notebook. Status per row:

  | Status | Meaning | Your action |
  | --- | --- | --- |
  | `unprocessed` | Ready for you | Read or listen, then set `done` |
  | `generating` | Podcast still being generated | Wait |
  | `ready` | Notebook and podcast are ready | Listen, then set `done` |
  | `failed` | Generation failed or timed out; for a category item the processing stopped (a session failed, the note names no URL, the page could not be fetched). The `link` column says why | Re-run the item (UI or `run_flow`) or set `manual` and handle it yourself |
  | `manual` | You took it over | Set `done` when finished |
  | `done` | Finished | – |

- **What else appears:** the INBOX note of a processed item moves to `4-Archive/Inbox/`; a Misc item that suggests a new theme adds a note `2-Areas/Agent-Tasks/new-category-<name>-<id>.md` with an open task: add the category to `categories.json` (UI or overlay) or delete the note.
- **Tip:** if a note is wrong or weak, correct it in place. Corrected notes are a feedback signal for the session eval datasets (see [Evaluation](evaluation.md)).

### Review quarantine

- **What:** decide whether quarantined items are real attacks or false positives.
- **When:** after the daily summary, if it reports quarantined items.
- **How:**
  1. Open `0-INBOX/_quarantine/`. Each file `<item id>.md` holds the original note in inert form (inside a code block) with `security_flag` front matter: flags, detectors, reason codes, run id, time. The row in the triage file says `quarantined`; an item flagged at triage has the suggested category `none`.
  2. Read the evidence. **Do not open the original URL** in a normal browser and do not copy the content into another AI tool.
  3. **Real attack:** leave it. The URL is already in `security/malicious_urls.jsonl` of the overlay and the domain is on the overlay's deny-list (`# pkpipe: flagged N` counts repeats; a protected domain such as github.com is not added, only the log row exists). Delete the quarantine file whenever you like.
  3b. **What the quarantine shows (spec 029):** each quarantined row on the triage page shows the page address, the flags with their scores and, for the classifier, what each model said and a short reading (for example *only the injection detector fires and the safety model says Safe: the pattern of a false positive on markup-heavy pages*). The note in `0-INBOX/_quarantine/` holds the same in its front matter and a *Why it was flagged* table; it never holds more than the inert text.
  3c. **Held, not quarantined:** if only the injection detector fires, the item is *held for your decision* (a failed row with the evidence and **Accept warning and process**). Accept it to send the page through the next run (the other detectors still apply), or leave it.
  4. **False positive:** release it on the UI triage page ("Release from quarantine") or with the app MCP tool `release_quarantine(item_id)` (owner token). The note goes back to its INBOX path unchanged, the row becomes `pending` (an item flagged at triage gets a new suggestion at 20:00) and its text is written to `<overlay>/evals/candidates/security.jsonl` as a benign case; review the candidates and move them into `evals/datasets/security.jsonl` so the next eval measures the false positive. The deny-list entry is not removed automatically: if the domain is trustworthy, delete its line from `security/deny_list.txt` in the overlay. A release is refused while a new note occupies the original path.

### React to a security alert

- **What:** a `canary_leak`, or an unusual burst of flags, sent as an immediate alert.
- **When:** as soon as you see it.
- **How:** a single `malicious_content` or `injection_or_jailbreak` flag is handled by the quarantine review above. A **canary leak** means a system instruction left the session: open the run in the trace backend (the alert links the trace id), check which session and input caused it, and pause the affected category (`enabled: false` in the overlay) until the cause is understood. A canary leak is also a rollback criterion (see [Operations](operations.md#release-ladder)).

## Weekly (Sunday)

### Review context pack diff

- **What:** check the rebuilt context packs (`personal.md`, `enterprise.md`) that sessions use for fit evaluations.
- **When:** after the Sunday 19:00 "review diff" notification.
- **How:** open the review notes `2-Areas/Context-Review/<pack>.md` named in the notification (a diff of the pack). Check that the enterprise pack names no client, project or person you would not want in a prompt (code removes the names it knows; a name that appears only in the prose and that the local model neither hides nor reports can slip through, so this check is yours) and that nothing is outdated. Add lasting rules below the `<!-- pkpipe:manual -->` line of the pack: that text is never rewritten. If you edit the generated part, the refresh leaves the pack alone and writes its new version to `contexts/<pack>.md.proposed` with the diff in the review note: merge what you want, or `mv contexts/<pack>.md.proposed contexts/<pack>.md`. Commit the overlay (the UI or git) so the next config load carries the packs. Add private names that the notes never mention to `security/query_guard_terms.txt`; the refresh only appends to it.

### Weekly improvement review

*Only if the self-improvement cycle is enabled in your overlay.*

- **What:** decide what happens to each improvement item from the past week.
- **When:** after the Sunday 19:30 "review ready" notification.
- **How:** for each item the agent proposes a verdict (**keep**, **adjust**, **drop**) with evidence and a next experiment. Confirm or change the verdict. Kept personal items move into the personal context; items in the **system** area become a configuration change and go through the eval gate (see [Publish a configuration change](#publish-a-configuration-change)).

### Pick system tool candidates

- **What:** decide which tools flagged as "could improve pkpipe itself" deserve a spike.
- **When:** during the weekly review.
- **How:** open `2-Areas/System/System-Tool-Candidates.md` (or run `pkpipe candidates list`). The agent only adds rows (status `candidate`, with component, benefit, effort and a link to the evaluation note); every status change is yours. Mark the candidates worth a spike with `pkpipe candidates set <tool> --status investigating`, then `trial`. A spike means trying the tool behind the matching adapter (a second pack or overlay) and comparing it with the current component on the eval suite: `pkpipe candidates ab <tool> --candidate-overlay <spike>` writes an A/B report. Adopt only if the verdict is `better` (gate green, no gated metric worse, an improvement or a judge preference): `pkpipe candidates adr <tool> --ab-report <report>` drafts the ADR in `docs/decisions/` ([template and spike checklist](decisions/template.md)), then `pkpipe candidates set <tool> --status adopted --adr <file> --ab-report <report>`; the command refuses without both. Otherwise mark it rejected with `--reason`; a rejected tool is not proposed again unless you reopen it with `--evidence` (new evidence: the evidence of an earlier reopening does not count again). Adopting still means publishing the configuration change through the eval gate.

### Check the eval report

- **What:** the weekly full eval run and production metrics (override rate, quarantine rate, learning failures).
- **When:** when the weekly report arrives.
- **How:** look for red gates and rising trends. Override rate for a category above twice the weekly average means its triage hint or instructions need work. A red gate blocks publishing until fixed.

## Occasional tasks

### Publish a configuration change

- **When:** you change a category, instruction, schedule, model or guard threshold.
- **How:** edit in the UI (edits are drafts) or in the overlay repository. Publish runs schema validation, reference checks and the smoke eval for the affected step. Only a green publish is committed. Running flows finish on the old config version.

### Calibrate the LLM judge

- **When:** once after setup, and after a rubric change.
- **How:** score 20 session outputs yourself on the 1–5 rubric in the UI. Where the judge and you often differ by more than a point, refine the rubric.

### Supervise a release

- **When:** after upgrading the engine tag or changing a model.
- **How:** follow the release ladder (eval → shadow → canary → production). During shadow (3–7 days) pkpipe writes to `_staging` without alerts; compare a few notes with production before you promote. Roll back to the previous tag if a rollback criterion fires.

### Re-triage an item

- **When:** a category was removed from config while an item was open.
  - An item you had **not** decided on yet is suggested again automatically by the next 20:00 triage (it shows up as a new item); confirm or override the new suggestion.
  - An item you had **confirmed** (or overridden to a category that does not exist) is never released. The 20:00 notification says "N item(s) name a category that is no longer configured", even on evenings without new items.
- **How:** write an existing category id into **override** in `Inbox-Triage.md`. pkpipe never re-suggests a confirmed item, because your tick belongs to the old category.

### Undo a run

- **When:** a run wrote something wrong into the vault (a bad move, a wrong note, a production incident).
- **How:** `pkpipe snapshot list` shows the runs that took a snapshot. `pkpipe restore <run>` prints the plan and changes nothing; read it, then add `--apply`. Notes you edited after the run are skipped and listed (use `--force` only when you want the run's state back; your version is saved in the snapshot's `trash/` folder). The state before the restore is saved too, so `pkpipe restore restore-<run>-...` undoes a restore. The run page of the UI and the app MCP tool `restore_run` do the same. The app DB, the learning service and messages already sent are not part of it.

### Keep the machine healthy

| What | When | How |
| --- | --- | --- |
| Model server running with the configured model | After reboots, if the summary reports model errors | Start LM Studio / Ollama; check with `curl $PKPIPE_MODEL_BASE_URL/models` |
| Back up app DB, overlay repo and trace volume | Weekly | Copy `pkpipe.db`, push the overlay repo, back up the `phoenix-data` volume. The snapshots (`$PKPIPE_DATA_DIR/snapshots`) are a short-lived safety net, not a backup of the vault |
| Update the engine | When a new tag is released | Pin the new tag in the deployment repo, then supervise the release |
| Rotate secrets | When a webhook or API key may have leaked, or yearly | Replace the keychain or env value; config only references `env:NAME` |

Missed schedules (machine asleep) run once on wake, so you do not need to re-run anything by hand after a night with the lid closed.
