# 💭 Reflection: Game Glitch Investigator

Answer each question in 3 to 5 sentences. Be specific and honest about what actually happened while you worked. This is about your process, not trying to sound perfect.

## 1. What was broken when you started?

- What did the game look like the first time you ran it?
- List at least two concrete bugs you noticed at the start  
  - Confusing hints that sometimes say I should go higher and other times say I should go lower even though I'm submitting the same number
  - The range for normal difficulty was larger than hard difficutly
  - It felt like there was a lag when submitting answers and it appearing in the history. It only appeared after submitting yet another answer.
  - Normal mode had more attempts than easy mode
  - If the difficulty is changed, the limits may be changed but the secret doesn't. So it becomes possible for the secret to fall outside the range.
  - The maximum points possible were 90 and not 100
  - The logic for new games felt broken

---

## 2. How did you use AI as a teammate?

- Which AI tools did you use on this project (for example: ChatGPT, Gemini, Copilot)?
  - I used kiro-cli because I have it set up for work.
- Give one example of an AI suggestion that was correct (including what the AI suggested and how you verified the result).
  - Kiro had informed me of the inverted hints. It was right about that, but it missed the bigger point of different data being passed in depending on whether the number of attempts was even or odd.
- Give one example of an AI suggestion that was incorrect or misleading (including what the AI suggested and how you verified the result).
  - Kiro kept complaining about a secret reset issue that I didn't observe while testing the app.
  - The secret logic was a bit iffy because it was passed as different types but it wasn't reset.

---

## 3. Debugging and testing your fixes

- How did you decide whether a bug was really fixed?
  - After reviewing the code to make sure it makes sense, I deployed the application and tested the case it was struggling with.
- Describe at least one test you ran (manual or using pytest) and what it showed you about your code.
  - I ran the application and kept inputting the same guess to see if the hints will flip-flop or if they will stay consistent and meaningful.
- Did AI help you design or understand any tests? How?
  - Yes, I had it write the unit tests initially. I made some fixes/additions to them afterward.

---

## 4. What did you learn about Streamlit and state?

- In your own words, explain why the secret number kept changing in the original app.
  - It didn't change. It was just passed as either an int or a string depending on the number of attempts. When the secret is passed as a string, the guess (an int) is compared to a string and this causes strange behavior.
- How would you explain Streamlit "reruns" and session state to a friend who has never used Streamlit?
  - Streamlit acts like a script that runs from top to bottom. Reruns make the script re-run from the top.
- What change did you make that finally gave the game a stable secret number?
  - I removed the unnecessary even/odd logic that changes the type of the secret.

---

## 5. Looking ahead: your developer habits

- What is one habit or strategy from this project that you want to reuse in future labs or projects?
  - I may use kiro-cli more as it was slightly more useful than I expected.
- What is one thing you would do differently next time you work with AI on a coding task?
  - I'm considering configuring my own agent with its own skills and MCP server.
- In one or two sentences, describe how this project changed the way you think about AI generated code.
  - AI code is more slopy than I thought... Or perhaps this code had bugs injected or was written before AI code became better.
