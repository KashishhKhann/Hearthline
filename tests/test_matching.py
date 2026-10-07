import pytest

from constants import contains_word


@pytest.mark.parametrize(
    "text, term, expected",
    [
        ("i reported the damp", "rte", False),        # the original media_risk bug
        ("citynorth quarter", "rte", False),
        ("a reporter from rte called", "rte", True),
        ("rté investigates", "rté", True),
        ("full plumbing inspection", "bin", False),   # the original bin_collection bug
        ("combined with the lift outage", "bin", False),
        ("the wheelie bins blew over", "bin", True),
        ("water is leaking", "leak", True),           # inflections still match
        ("it leaked overnight", "leak", True),
        ("rated five stars", "rat", False),           # short terms: plural only
        ("we saw rats", "rat", True),
        ("that was helpful", "help", False),
        ("my parent visited", "rent", False),
        ("the paralegal wrote", "legal", False),
        ("we are against it", "again", False),
        ("two contractors came", "contractor", True),
    ],
)
def test_contains_word(text, term, expected):
    assert contains_word(text, {term}) is expected


def test_multi_word_phrase_is_substring():
    assert contains_word("please post on social media now", {"post on social media"})
