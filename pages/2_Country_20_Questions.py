"""
Country 20 Questions — Streamlit page.

Game loop:
  1. Game starts: pick a secret country, reset counters.
  2. Player types a natural-language yes/no question OR a final guess
     ("Is it Japan?").
  3. classifier.classify_question() asks Gemini (or falls back to a keyword
     rule-based classifier if no API key) to translate into a structured
     Query against the field catalog. The LLM never sees the secret.
  4. country_logic.evaluate() runs the Query deterministically against the
     secret. A name-equals query is a final guess: correct ends the game,
     wrong costs a question.
  5. Game ends on correct guess or when questions run out.
"""

from __future__ import annotations

import random

import streamlit as st

from classifier import ClassifyResult, classify_question
from country_logic import (
    DIFFICULTY_LIMITS,
    answer_text,
    evaluate,
    find_country_by_guess,
    load_countries,
    pick_secret,
    score_for_win,
)

# ---------------------------------------------------------------------------
# Page config & constants
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Country 20 Questions", page_icon="🌍")

# Namespaced session-state keys. Prefixing with "c20q_" prevents collisions
# with the number-game page which uses unprefixed keys like "secret".
S_SECRET = "c20q_secret"
S_QUESTIONS_ASKED = "c20q_questions"
S_HISTORY = "c20q_history"           # list of dicts: {question, result, reason, source}
S_STATUS = "c20q_status"             # "playing" | "won" | "lost"
S_PREV_DIFFICULTY = "c20q_prev_difficulty"
S_GAME_HISTORY = "c20q_game_history"  # list of completed-game dicts
S_SCORE = "c20q_score"

STATUS_PLAYING = "playing"
STATUS_WON = "won"
STATUS_LOST = "lost"


# ---------------------------------------------------------------------------
# State init and reset
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def _countries() -> list[dict]:
    """Load the dataset once per session."""
    return load_countries()


def _start_new_game(countries: list[dict]) -> None:
    st.session_state[S_SECRET] = pick_secret(countries, rng=random.Random())
    st.session_state[S_QUESTIONS_ASKED] = 0
    st.session_state[S_HISTORY] = []
    st.session_state[S_STATUS] = STATUS_PLAYING


def _ensure_state(countries: list[dict], difficulty: str) -> None:
    if S_PREV_DIFFICULTY not in st.session_state:
        st.session_state[S_PREV_DIFFICULTY] = difficulty
    if S_GAME_HISTORY not in st.session_state:
        st.session_state[S_GAME_HISTORY] = []
    if S_SCORE not in st.session_state:
        st.session_state[S_SCORE] = 0
    if S_SECRET not in st.session_state:
        _start_new_game(countries)


def _record_game_end(difficulty: str, result: str) -> None:
    """Append to the per-page game history and update cumulative score."""
    questions_used = st.session_state[S_QUESTIONS_ASKED]
    points = score_for_win(questions_used) if result == "Won" else 0
    st.session_state[S_SCORE] += points
    st.session_state[S_GAME_HISTORY].append(
        {
            "result": result,
            "difficulty": difficulty,
            "questions": questions_used,
            "country": st.session_state[S_SECRET]["name"],
            "score": points,
        }
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("🌍 Country 20 Questions")
st.caption(
    "I'm thinking of a country. Ask yes/no questions to narrow it down, "
    "or make a final guess when you're ready."
)

countries = _countries()

# Sidebar
st.sidebar.header("Settings")
difficulty = st.sidebar.selectbox(
    "Difficulty",
    options=list(DIFFICULTY_LIMITS.keys()),
    index=1,
    help="Controls how many questions (incl. final guesses) you get.",
)
question_limit = DIFFICULTY_LIMITS[difficulty]

_ensure_state(countries, difficulty)

# Difficulty change resets the game so the question budget stays consistent.
if st.session_state[S_PREV_DIFFICULTY] != difficulty:
    st.session_state[S_PREV_DIFFICULTY] = difficulty
    _start_new_game(countries)
    st.info(f"Difficulty changed to {difficulty}. New game started.")

st.sidebar.caption(f"Question limit: {question_limit}")
st.sidebar.caption(f"Cumulative score: {st.session_state[S_SCORE]}")

st.sidebar.divider()
st.sidebar.subheader("Game History")
if st.session_state[S_GAME_HISTORY]:
    for i, g in enumerate(st.session_state[S_GAME_HISTORY], 1):
        icon = "✅" if g["result"] == "Won" else "❌"
        st.sidebar.text(
            f"{icon} {g['result']} — {g['country']} "
            f"({g['difficulty']}, {g['questions']}Q, +{g['score']})"
        )
else:
    st.sidebar.caption("No games completed yet.")

# Main area — attempts remaining
questions_left = question_limit - st.session_state[S_QUESTIONS_ASKED]
col_info, col_newgame = st.columns([3, 1])
with col_info:
    st.info(f"Questions left: **{questions_left}** of {question_limit}")
with col_newgame:
    if st.button("New Game 🔁", use_container_width=True):
        _start_new_game(countries)
        st.rerun()

# If game is over, show result and stop the interactive flow.
if st.session_state[S_STATUS] != STATUS_PLAYING:
    secret = st.session_state[S_SECRET]
    if st.session_state[S_STATUS] == STATUS_WON:
        st.success(
            f"🎉 You got it! The country was **{secret['flag']} {secret['name']}**."
        )
    else:
        st.error(
            f"Out of questions. The country was **{secret['flag']} {secret['name']}**."
        )
    # Still render history below so players can review.

# ---------------------------------------------------------------------------
# Question input
# ---------------------------------------------------------------------------

if st.session_state[S_STATUS] == STATUS_PLAYING:
    st.subheader("Ask a question")
    with st.form("question_form", clear_on_submit=True):
        question = st.text_input(
            "Your yes/no question (or final guess):",
            placeholder='e.g. "Is it in Europe?" or "Is it Japan?"',
        )
        submitted = st.form_submit_button("Ask 🎯")

    if submitted and question.strip():
        with st.spinner("Thinking..."):
            result: ClassifyResult = classify_question(question)

        st.session_state[S_QUESTIONS_ASKED] += 1
        secret = st.session_state[S_SECRET]
        entry: dict = {
            "question": question,
            "source": result.source,
        }

        if result.query is None:
            # Out of schema or classifier error — answer "I don't know"
            # and still charge a question (costs the player to ask unanswerable ones).
            entry["answer"] = None
            entry["reason"] = result.reason or "out of schema"
        elif result.query.field == "name":
            # Final-guess branch: do a fuzzy lookup against the dataset so
            # "South Korea" matches "Republic of Korea", etc.
            guessed = find_country_by_guess(str(result.query.value), countries)
            if guessed is not None and guessed["cca3"] == secret["cca3"]:
                entry["answer"] = True
                entry["reason"] = f"Correct! You guessed {secret['name']}."
                st.session_state[S_STATUS] = STATUS_WON
                _record_game_end(difficulty, "Won")
            else:
                entry["answer"] = False
                label = guessed["name"] if guessed else str(result.query.value)
                entry["reason"] = f"Not {label}."
        else:
            # Normal structured query — deterministic yes/no.
            entry["answer"] = evaluate(result.query, secret)
            entry["query"] = result.query.as_dict()

        st.session_state[S_HISTORY].insert(0, entry)

        # Lose condition after appending so the ran-out-of-questions message
        # includes the question that used up the budget.
        if (
            st.session_state[S_STATUS] == STATUS_PLAYING
            and st.session_state[S_QUESTIONS_ASKED] >= question_limit
        ):
            st.session_state[S_STATUS] = STATUS_LOST
            _record_game_end(difficulty, "Lost")

        st.rerun()


# ---------------------------------------------------------------------------
# History display
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Q&A history")
history = st.session_state.get(S_HISTORY, [])
if not history:
    st.caption("No questions asked yet.")
else:
    for i, entry in enumerate(history, 1):
        n = len(history) - i + 1
        label = answer_text(entry["answer"])
        icon = {"Yes": "✅", "No": "❌", "I don't know": "🤷"}[label]
        source_badge = f"`{entry['source']}`"
        with st.container(border=True):
            st.markdown(f"**Q{n}.** {entry['question']}")
            st.markdown(f"{icon} **{label}** — {entry.get('reason') or '' } {source_badge}")
            if "query" in entry:
                st.caption(f"Parsed as: `{entry['query']}`")

# ---------------------------------------------------------------------------
# Debug expander (mirrors the number-game page's convention)
# ---------------------------------------------------------------------------

with st.expander("Developer Debug Info"):
    st.write("Secret:", st.session_state[S_SECRET]["name"])
    st.write("CCA3:", st.session_state[S_SECRET]["cca3"])
    st.write("Questions asked:", st.session_state[S_QUESTIONS_ASKED])
    st.write("Status:", st.session_state[S_STATUS])
    st.write("Full secret record:", st.session_state[S_SECRET])
