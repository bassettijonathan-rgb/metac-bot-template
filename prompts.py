"""
Prompt text for research and forecasting.

Pure strings, no third-party imports, so the assembly can be unit-tested.
Prompts are assembled by joining blocks rather than by indenting f-strings,
because question text and research are multi-line and would break dedenting.
"""
from __future__ import annotations

from textwrap import dedent

from config import INSIDE_VIEW, OUTSIDE_VIEW, SKEPTIC


def _block(text: str) -> str:
    return dedent(text).strip()


def _join(*parts: str | None) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


# --------------------------------------------------------------------------
# Research
# --------------------------------------------------------------------------

_RESEARCH_HEADER = _block(
    """
    You are the research assistant of a professional forecaster. Search the web
    and report facts. You do not give a forecast or a probability of your own.

    Rules:
    - Every fact gets its date and its source (publication name and URL).
    - Prefer primary sources (official statistics, regulators, company filings,
      court documents, the resolution source named in the question) over
      commentary.
    - If you cannot find something, say "not found". Never fill a gap with a guess.
    - Report conflicting accounts side by side instead of picking one.
    - Be concise: bullet points, no introduction, no conclusion.
    """
)

RESEARCH_PASSES: dict[str, tuple[str, str]] = {
    # key: (section title shown to the forecasters, task text)
    "status": (
        "Current status and recent news",
        _block(
            """
            Task for this pass: CURRENT STATUS.
            1. Where things stand today against the exact resolution criteria.
               State plainly if the outcome already looks decided, and by what.
            2. The latest value or reading from the resolution source, if the
               question names one, with its date.
            3. What changed in the last 30 days that bears on the outcome.
            4. Scheduled events between today and the resolution date that could
               decide it (votes, hearings, data releases, deadlines, matches).
            5. Anything in the fine print that could make the question resolve
               differently from what the headline suggests.
            """
        ),
    ),
    "base_rates": (
        "Base rates and outside forecasts",
        _block(
            """
            Task for this pass: BASE RATES AND OUTSIDE FORECASTS.
            1. Historical frequency: how often did this kind of event happen in
               comparable past situations? Give counts and periods (for example
               "4 of the last 20 years"). Offer two different reference classes
               if you can.
            2. For numeric questions: the recent history of the series (last
               values, typical change over a period as long as the time left,
               largest moves on record).
            3. What prediction markets, bookmakers, futures or options prices,
               analyst consensus or official projections currently imply. Quote
               the numbers and their dates.
            4. Named experts or institutions with a public forecast, and what
               they said.
            """
        ),
    ),
    "combined": (
        "Research",
        _block(
            """
            Task: report, in this order,
            1. Where things stand today against the exact resolution criteria,
               including the latest reading from the resolution source and
               whether the outcome already looks decided.
            2. What changed in the last 30 days, and the scheduled events before
               the resolution date that could decide it.
            3. Base rates: how often this kind of event happened in comparable
               past situations, with counts and periods. For numeric questions,
               the recent history and typical size of moves.
            4. What prediction markets, bookmakers, futures, analyst consensus
               or official projections currently imply, with numbers and dates.
            """
        ),
    ),
}


def research_prompt(
    pass_key: str,
    *,
    question_text: str,
    resolution_criteria: str | None,
    fine_print: str | None,
    background: str | None,
    today: str,
    resolution_date: str | None,
) -> str:
    _, task = RESEARCH_PASSES[pass_key]
    return _join(
        _RESEARCH_HEADER,
        f"Today is {today}."
        + (f" The question is due to resolve around {resolution_date}." if resolution_date else ""),
        f"Question:\n{question_text}",
        f"Resolution criteria:\n{resolution_criteria}" if resolution_criteria else None,
        f"Fine print:\n{fine_print}" if fine_print else None,
        f"Background given by the question writer:\n{background}" if background else None,
        task,
    )


def research_section_title(pass_key: str) -> str:
    return RESEARCH_PASSES[pass_key][0]


NO_RESEARCH_NOTE = (
    "No research could be retrieved for this question. Rely on background "
    "knowledge, say so in your rationale, and stay closer to the base rate "
    "than you otherwise would."
)


# --------------------------------------------------------------------------
# Forecasting: one method block per framing
# --------------------------------------------------------------------------

_ROLE = _block(
    """
    You are a professional forecaster with a strong track record in forecasting
    tournaments. Your forecast is scored with a logarithmic rule against other
    forecasters: being confidently wrong is punished far more than being
    confidently right is rewarded, and hedging at the middle when the evidence
    is clear also loses points. Aim to be calibrated.
    """
)

_METHODS: dict[str, str] = {
    OUTSIDE_VIEW: _block(
        """
        Work through these steps in order and write each one down briefly.
        (1) Resolution check: restate exactly what must be true, by when and
            according to which source, for each outcome. Note any technicality.
        (2) Outside view first: pick the best reference class (two if they
            disagree) and state the base rate as a number before looking at the
            specifics of this case.
        (3) Time: how long until the outcome is known, and how the chance scales
            with the time left. For "will X happen by date D" questions, convert
            the base rate to the remaining window.
        (4) Inside view: the specific evidence from the research that should
            move you away from the base rate. Separate verified facts from
            speculation and weight recent, primary sources more.
        (5) Outside forecasts: what markets, bookmakers or expert consensus
            imply, if the research found any. Treat liquid markets as strong
            evidence.
        (6) Adjust: say how far you move from the base rate and why. Most cases
            deserve a moderate adjustment.
        (7) Check: describe the most plausible way you turn out wrong.
        """
    ),
    INSIDE_VIEW: _block(
        """
        Work through these steps in order and write each one down briefly.
        (1) Resolution check: restate exactly what must be true, by when and
            according to which source, for each outcome. Note any technicality.
        (2) Causal path first: list the concrete steps that must happen for each
            outcome, who decides each step, and which scheduled events lie
            between today and the resolution date.
        (3) Estimate how likely each step is given the research, and combine
            them. Remember that chains of several steps are less likely than
            each step looks alone, and that there is usually more than one path.
        (4) Status quo: what happens if nothing changes, and how much time is
            left for something to change.
        (5) Only now compare with the outside view: a base rate from a sensible
            reference class and any market or expert numbers in the research.
            If your estimate is far from them, explain the gap or move toward
            them.
        (6) Check: describe the most plausible way you turn out wrong.
        """
    ),
    SKEPTIC: _block(
        """
        Your job in this team is to be the skeptic who protects the forecast
        from an attractive story. Work through these steps and write each one
        down briefly.
        (1) Read the resolution criteria and fine print like a contract. List
            every way the question could resolve differently from what the
            headline narrative suggests: exact thresholds, dates and time zones,
            which source counts, what happens if data is revised, delayed or
            not published.
        (2) State the narrative the news coverage pushes, then list what would
            have to be true for it to be wrong.
        (3) Status quo and inertia: the world changes slowly most of the time.
            What is the outcome if nothing changes before the resolution date?
        (4) Base rate: how often do predictions of this kind of change come
            true within a window this short?
        (5) Weigh the research. Discount unsourced claims, single anonymous
            sources and commentary. Give full weight to hard data and to
            anything that has already happened.
        (6) Give your own honest estimate. Being the skeptic does not mean
            always betting against change: if the evidence is hard, follow it.
        """
    ),
}

_CALIBRATION_BINARY = _block(
    """
    Calibration rules:
    - Do not go above 97% or below 3% unless the outcome is essentially settled
      by verifiable facts that have already happened.
    - Do not retreat to 40-60% just to be safe. If the evidence clearly points
      one way, say so.
    - Small differences matter at the extremes: 2% and 8% are very different
      forecasts.
    """
)

_FORMAT_BINARY = 'The last thing you write is your final answer as: "Probability: ZZ%", 0-100'


def _question_blocks(
    *,
    question_text: str,
    background: str | None,
    resolution_criteria: str | None,
    fine_print: str | None,
    research: str,
    today: str,
    resolution_date: str | None,
    extra: str | None = None,
) -> list[str | None]:
    return [
        f"The question:\n{question_text}",
        extra,
        f"Background:\n{background}" if background else None,
        (
            "Resolution criteria (these have not yet been satisfied):\n"
            f"{resolution_criteria}"
            if resolution_criteria
            else None
        ),
        f"Fine print:\n{fine_print}" if fine_print else None,
        f"Your research assistant reports:\n{research}",
        f"Today is {today}."
        + (f" The question is due to resolve around {resolution_date}." if resolution_date else ""),
    ]


def binary_prompt(framing: str, *, conditional_disclaimer: str = "", **fields) -> str:
    return _join(
        _ROLE,
        *_question_blocks(**fields),
        _METHODS[framing],
        conditional_disclaimer,
        _CALIBRATION_BINARY,
        _FORMAT_BINARY,
    )


_CALIBRATION_MC = _block(
    """
    Calibration rules:
    - Leave some probability on every option that is not ruled out by facts
      that have already happened. Surprises are more common than they feel.
    - Never give an option exactly 0% unless it is impossible.
    - The probabilities must add up to 100%.
    """
)


def multiple_choice_prompt(
    framing: str, *, options: list[str], conditional_disclaimer: str = "", **fields
) -> str:
    output_format = _join(
        f"The last thing you write is your final probabilities for the options in this order {options} as:",
        "Option_A: Probability_A\nOption_B: Probability_B\n...\nOption_N: Probability_N",
    )
    return _join(
        _ROLE,
        *_question_blocks(extra=f"The options are: {options}", **fields),
        _METHODS[framing],
        conditional_disclaimer,
        _CALIBRATION_MC,
        output_format,
    )


_CALIBRATION_NUMERIC = _block(
    """
    Calibration rules:
    - Start from the latest known value and the typical size of moves over a
      period as long as the time left, then adjust for the specific evidence.
    - Set wide tails. Forecasters lose the most points when the outcome lands
      outside their range, and unknown unknowns are real. Your 5th and 95th
      percentiles should be values you would be genuinely surprised to see
      crossed, not merely unlikely ones.
    - Do not make the distribution symmetric out of habit. If one side has the
      fatter tail, show it.
    """
)

_NUMERIC_FORMATTING = _block(
    """
    Formatting instructions:
    - Notice the units requested and give your answer in those units (for
      example whether a number is written as 1,000,000 or as 1 million).
    - Never use scientific notation.
    - Always start with the smallest number (most negative if negative) and
      increase from there. Each percentile value must be larger than the one
      before it.
    """
)

_FORMAT_NUMERIC = _block(
    """
    The last thing you write is your final answer as:
    "
    Percentile 5: XX (lowest number value)
    Percentile 10: XX
    Percentile 20: XX
    Percentile 40: XX
    Percentile 50: XX
    Percentile 60: XX
    Percentile 80: XX
    Percentile 90: XX
    Percentile 95: XX (highest number value)
    "
    """
)


def numeric_prompt(
    framing: str,
    *,
    unit_of_measure: str | None,
    lower_bound_message: str,
    upper_bound_message: str,
    conditional_disclaimer: str = "",
    **fields,
) -> str:
    units = unit_of_measure if unit_of_measure else "Not stated (please infer this)"
    return _join(
        _ROLE,
        *_question_blocks(extra=f"Units for answer: {units}", **fields),
        f"{lower_bound_message}\n{upper_bound_message}",
        _METHODS[framing],
        conditional_disclaimer,
        _CALIBRATION_NUMERIC,
        _NUMERIC_FORMATTING,
        _FORMAT_NUMERIC,
    )


_DATE_FORMATTING = _block(
    """
    Formatting instructions:
    - This is a date question, so the answer must be expressed as dates.
    - Write dates as YYYY-MM-DD. If hours matter, append the hour in UTC and
      military time: YYYY-MM-DDTHH:MM:SSZ. No other format is allowed.
    - The dates must be in chronological order, starting with the earliest date
      at the lowest percentile and increasing from there.
    """
)

_FORMAT_DATE = _block(
    """
    The last thing you write is your final answer as:
    "
    Percentile 5: YYYY-MM-DD (earliest date)
    Percentile 10: YYYY-MM-DD
    Percentile 20: YYYY-MM-DD
    Percentile 40: YYYY-MM-DD
    Percentile 50: YYYY-MM-DD
    Percentile 60: YYYY-MM-DD
    Percentile 80: YYYY-MM-DD
    Percentile 90: YYYY-MM-DD
    Percentile 95: YYYY-MM-DD (latest date)
    "
    """
)


def date_prompt(
    framing: str,
    *,
    lower_bound_message: str,
    upper_bound_message: str,
    conditional_disclaimer: str = "",
    **fields,
) -> str:
    return _join(
        _ROLE,
        *_question_blocks(**fields),
        f"{lower_bound_message}\n{upper_bound_message}",
        _METHODS[framing],
        conditional_disclaimer,
        _CALIBRATION_NUMERIC,
        _DATE_FORMATTING,
        _FORMAT_DATE,
    )
