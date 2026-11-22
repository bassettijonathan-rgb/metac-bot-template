# Ensemble forecasting bot (Metaculus FutureEval)

Files in this folder are meant to be copied on top of a fork of
[Metaculus/metac-bot-template](https://github.com/Metaculus/metac-bot-template)
(template commit `bcbd016`, 2026-10-30). The template's `main.py`,
`bot_helpers.py`, `pyproject.toml` and `uv.lock` stay untouched.

## How the bot works (description for prize verification)

1. Every 20 minutes a GitHub Actions job asks Metaculus for open questions in
   the current FutureEval season and in MiniBench that the bot has not answered.
2. Research: for each question, independent web-search passes run in parallel
   ("current status and recent news", "base rates and outside forecasts"), plus
   AskNews when keys are set. Search uses the model provider's native search
   through OpenRouter's web search tool, with a cap on the number of searches
   per pass (`max_searches` in `config.py`). A failed pass is skipped, never
   fatal.
3. Forecast: 3 to 5 ensemble members answer independently. Members differ by
   model family (Anthropic, OpenAI, Google) and by reasoning order: outside view
   first, inside view first, or skeptic (reads the resolution criteria like a
   contract and argues against the news narrative).
4. Aggregation: binary questions use a trimmed mean in log-odds (highest and
   lowest dropped when there are 5 forecasts), clipped to 2%-98%, with no
   extremization. Multiple choice, numeric and date questions use the
   aggregation of the forecasting-tools library. Numeric and date prompts ask
   for explicit 5th and 95th percentiles.
5. The forecast and a private reasoning comment are submitted through the API.
   No human reviews or edits individual forecasts.
6. Budget guard: at start-up the bot reads the remaining OpenRouter credit and
   steps down to cheaper models (profiles in `config.py`) as it runs low.
7. Cost report: each run ends with a table of what OpenRouter charged per
   stage (web search, forecast, helper) and the credit before and after.

## Files

- `bot.py`: the bot (subclass of the template bot) and the entry point.
- `prompts.py`: research and forecasting prompts.
- `config.py`: model line-ups ("profiles"), search cap and budget thresholds.
- `costs.py`: records what each model call cost and prints the table per stage.
- `aggregation.py`: maths for combining binary forecasts.
- `check_setup.py`: read-only check of the Metaculus token, of the model names
  in `config.py` against OpenRouter's public list, and of the remaining credit.
  No model call, nothing submitted, costs nothing.
- `tests/`: unit tests with stand-ins for the template and the library, standard
  library plus `requests` (`python -m unittest discover -s tests`).
- `tests_real_library/`: runs the bot end to end on every question type
  against the real template and the real forecasting-tools library, with only
  the model call and the answer parser replaced
  (`uv run python -m unittest discover -s tests_real_library`). Kept in its own
  folder because `tests/` swaps the library for stand-ins.
- `.github/workflows/`: scheduled run, manual test run, setup check, unit tests.
  The Metaculus Cup schedule from the template is removed (bots win no prizes
  there).

## Settings (GitHub: Settings, Secrets and variables, Actions)

Secrets: `METACULUS_TOKEN` (required), `OPENROUTER_API_KEY` (required),
`ASKNEWS_CLIENT_ID` + `ASKNEWS_SECRET` or `ASKNEWS_API_KEY` (optional).
Variables: `BOT_PROFILE` = `auto` (default), `full`, `standard`, `lean`, `minimal`.
`TOURNAMENT_ENABLED` = `true` switches the tournament workflow on. Until it is
set, that workflow does nothing, on its schedule or by hand, so an untested bot
cannot forecast on scored questions.

## Going live, in order

1. "Setup check" (or "Test Bot" with `check_only` ticked): token, model names.
   Free. It also runs on pull requests that change `config.py`.
2. Add `OPENROUTER_API_KEY`, run "Test Bot" in full on the unscored
   bot-testing-area, read the cost per question, tune `config.py`. For a cheap
   trial after a change, run "Test Bot" with `limit` 2 and `dry_run` ticked:
   two questions of different types, nothing submitted, a few cents.
3. Set the variable `TOURNAMENT_ENABLED` to `true`. On a fork GitHub also keeps
   scheduled workflows off until "Enable workflow" is pressed on the workflow's
   page in the Actions tab.

## Status and known limits (2026-11-22)

- Unit tests pass (62), and so do the 12 end-to-end tests against the real
  template and forecasting-tools 0.3.5 (binary, multiple choice, numeric,
  discrete, date and conditional questions, the entry point and what it would
  submit, and the cost recording on the real model-call path).
- First full "Test Bot" run (2026-11-22, `minimal` profile, bot-testing-area):
  10 of 10 forecasts submitted, no errors, about 21 minutes. Web search through
  OpenRouter works.
- Measured cost, `minimal` profile, read from the OpenRouter credit:

  | Run | Questions | Searches per question | Cost per question |
  | --- | --- | --- | --- |
  | First full run, no cap | 10 | not recorded | $0.137 |
  | Trial, no cap | 2 | 10 and 17 | $0.158 |
  | Trial, cap of 4 | 2 (the same two) | 3 and 4 | $0.047 |

  Without a cap the web search call was 95% of the cost, and the per-search fee
  alone (about $0.01 each) was 86%. All forecasts and parsing together were 5%.
- What the cap costs in quality, on those two questions: the final forecasts
  barely moved (65/34/1 against 64/34/2; medians within about 2%), but the
  research was shorter (4 and 10 sources instead of 11 and 34) and on the
  numeric question it missed two official dollar projections and the community
  estimate that the uncapped run had found. Two questions prove nothing about
  accuracy: `max_searches` is a dial, 4 on `minimal`, 5 or 6 on the others.
- `lean`, `standard` and `full` have not been run. Their search model costs 20
  times more per token than the one in `minimal`, so there the pages read by
  the search weigh as much as the per-search fee. Expect several times the
  `minimal` cost; measure with a two-question trial before a season on them.
- The cap is sent three ways (`max_uses`, `max_tool_calls`, a line in the
  prompt). OpenRouter documents `max_uses` only for Anthropic's search and
  `max_tool_calls` only for its Responses endpoint, so with the OpenAI search
  model it may be the prompt line alone that holds it. The search count is in
  every run's cost table: check it after changing the search model.
- The "Total cost estimated" line printed by the library is about twice the
  real charge. Use the "Cost by stage" table and the credit difference.
- The fork matches the template at commit `bcbd016`. The library in `uv.lock`
  targets the Fall 2026 season (`CURRENT_AI_COMPETITION_ID`): before the next
  season starts, merge the template's update so the bot follows the new season.
- The budget guard reads the credit limit of the OpenRouter key. A key without
  a limit of its own reports nothing, and the bot then stays on `standard`.
- GitHub pauses scheduled workflows in a repository with no commits for 60
  days. Push something (a review note is enough) at least every 8 weeks.
- Rules to respect: do not preview open tournament questions and then change
  the bot; do not rerun a question because the forecast looks wrong.
