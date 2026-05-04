"""
Landing page. Streamlit auto-discovers the games under pages/ and shows them
in the sidebar navigation.
"""

import streamlit as st

st.set_page_config(page_title="Guessing Games", page_icon="🎲")

st.title("🎲 Guessing Games")
st.caption("A collection of guessing games built on top of Streamlit.")

st.markdown(
    """
Pick a game from the sidebar:

**🎮 Number Guesser** — the original "Game Glitch Investigator" number game.
Pick a difficulty, guess the secret number, and try to score as high as you
can before running out of attempts.

**🌍 Country 20 Questions** — a 20-questions style game where the computer
picks a secret country from the 193 UN member states and you narrow it down
by asking natural-language yes/no questions ("Is it in Europe?",
"Does it border France?", "Is it an island?"). An LLM translates each
question into a structured query; the yes/no is computed from a grounded
dataset so answers are accurate.
"""
)

st.divider()
st.caption(
    "Country answers come from a vendored snapshot of "
    "[REST Countries](https://restcountries.com/). "
    "Question interpretation uses Google Gemini when `GOOGLE_API_KEY` is set; "
    "a small keyword-based fallback is used otherwise."
)
