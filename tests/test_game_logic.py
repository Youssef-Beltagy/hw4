from logic_utils import check_guess, get_range_for_difficulty, parse_guess, update_score


# --- check_guess ---

def test_winning_guess():
    outcome, msg = check_guess(50, 50)
    assert outcome == "Win"
    assert msg == "🎉 Correct!"

def test_guess_too_high():
    outcome, msg = check_guess(60, 50)
    assert outcome == "Too High"
    assert msg == "📉 Go LOWER!"

def test_guess_too_low():
    outcome, msg = check_guess(40, 50)
    assert outcome == "Too Low"
    assert msg == "📈 Go HIGHER!"


# --- get_range_for_difficulty ---

def test_easy_range():
    assert get_range_for_difficulty("Easy") == (1, 20)

def test_normal_range():
    assert get_range_for_difficulty("Normal") == (1, 50)

def test_hard_range():
    assert get_range_for_difficulty("Hard") == (1, 100)


# --- parse_guess ---

def test_parse_valid_int():
    ok, val, err = parse_guess("42")
    assert ok and val == 42 and err is None

def test_parse_float_string():
    ok, val, err = parse_guess("3.7")
    assert ok and val == 3 and err is None

def test_parse_empty():
    ok, val, err = parse_guess("")
    assert not ok and err is not None

def test_parse_none():
    ok, val, err = parse_guess(None)
    assert not ok and err is not None

def test_parse_non_numeric():
    ok, val, err = parse_guess("abc")
    assert not ok and err == "That is not a number."


# --- update_score ---

def test_score_win_first_attempt():
    assert update_score(0, "Win", 1) == 100

def test_score_win_later_attempt():
    assert update_score(0, "Win", 5) == 60

def test_score_win_min_points():
    assert update_score(0, "Win", 20) == 10

def test_score_wrong_guess():
    assert update_score(100, "Too High", 1) == 95

def test_score_wrong_guess():
    assert update_score(100, "Too Low", 1) == 95

def test_score_wrong_guess():
    assert update_score(0, "Too High", 1) == -5
