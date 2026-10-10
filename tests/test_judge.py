"""KeywordJudge: how a typed definition is graded in mechanical study."""
import pytest

from vocab.study.judge import KeywordJudge

judge = KeywordJudge()


def test_feedback_states_the_verdict_from_the_pass_threshold():
    half = judge.score("large", "large amount")     # 1 of 2 key terms: a pass
    assert half.feedback == ("Accepted — you recalled enough key terms. "
                             "Key terms not mentioned: amount.")
    assert half.score == 0.5 and half.passed
    full = judge.score("a large amount", "large amount")
    assert full.feedback == "Accepted — you recalled every key term."
    assert full.passed
    low = judge.score("big", "large amount")
    assert low.feedback.startswith("Not accepted — at least 50% of the key terms are needed.")
    assert not low.passed


def test_feedback_names_missed_terms_as_written_in_the_definition():
    result = judge.score("brief", "lasting a very short time")
    assert result.feedback.endswith("Key terms not mentioned: lasting, very, short, time.")
    assert result.missed == {"lasting", "very", "short", "time"}
    # not as compared ("quantiti"), and in the definition's own case
    assert judge.score("large", "large quantities").feedback.endswith(
        "Key terms not mentioned: quantities.")
    assert judge.score("a city", "the capital of France").feedback.endswith(
        "Key terms not mentioned: capital, France.")


@pytest.mark.parametrize("answer, reference", [
    ("мимолётный", "мимолётный"),
    ("cafe\u0301", "café"),          # the accent typed as a combining mark
    ("finance", "\ufb01nance"),       # the "fi" ligature
    ("STRASSE", "Straße"),
    ("1984", "1984"),
    ("किताब", "किताब"),
])
def test_words_in_any_script_can_be_recalled(answer, reference):
    assert judge.score(answer, reference).score == 1.0


def test_accents_are_optional_only_on_latin_letters():
    assert judge.score("cafe", "café").score == 1.0
    assert judge.score("naïve", "naive").score == 1.0
    assert judge.score("resume", "résumé").score == 1.0
    assert judge.score("мои", "мой").score == 0.0                  # и is not й
    assert judge.score("мимолетный", "мимолётный").score == 0.0    # е is not ё
    assert judge.score("かかみ", "かがみ").score == 0.0             # nor か が
    assert judge.score("かか\u3099み", "かがみ").score == 1.0       # が typed as か + mark
    assert judge.score("कताब", "किताब").score == 0.0               # vowel signs count


def test_an_unrelated_answer_does_not_pass_a_definition_in_another_script():
    result = judge.score("a big elephant", "мимолётный кратковременный")
    assert result.score == 0.0 and not result.passed
    assert result.feedback.endswith("Key terms not mentioned: мимолётный, кратковременный.")


@pytest.mark.parametrize("answer, reference", [
    ("an ox", "an ox"),
    ("ox", "an ox"),
    ("go", "to go"),
    ("no", "no"),
    ("not any", "not any"),
])
def test_a_short_definition_is_compared_on_its_short_words(answer, reference):
    assert judge.score(answer, reference).score == 1.0


@pytest.mark.parametrize("answer, reference", [
    ("a big elephant", "an ox"),
    ("anything at all", "to go"),
    ("the", "an ox"),
    ("yes", "no"),
])
def test_an_unrelated_answer_does_not_pass_a_short_definition(answer, reference):
    result = judge.score(answer, reference)
    assert result.score == 0.0 and not result.passed
    assert result.feedback.startswith("Not accepted")


@pytest.mark.parametrize("reference", ["—", "?", "...", ""])
def test_a_definition_without_words_cannot_be_graded(reference):
    result = judge.score("anything", reference)
    assert result.score == 0.0 and not result.passed
    assert "cannot be graded" in result.feedback
    assert not result.gradable


@pytest.mark.parametrize("answer, reference", [
    ("making something unclear or obscured", "make obscure or unclear"),
    ("makes", "make"), ("horses", "horse"), ("stories", "story"),
    ("studied", "study"), ("carries", "carry"), ("running", "run"),
    ("stopped", "stop"), ("happily", "happy"), ("agreed", "agree"),
    ("using", "use"), ("hoping", "hope"), ("glasses", "glass"),
    ("applies", "apply"), ("a large quantity", "large quantities"),
    ("cafés", "café"), ("viruses", "virus"),
])
def test_regular_inflections_recall_the_reference_word(answer, reference):
    result = judge.score(answer, reference)
    assert result.score == 1.0
    assert result.feedback == "Accepted — you recalled every key term."


def test_regular_forms_of_one_word_all_match():
    # The older suffix rules matched most of these; they must keep matching.
    families = [
        ("know", "knows", "knowing", "knowingly"),
        ("add", "adds", "added", "adding"), ("tie", "ties", "tied", "tying"),
        ("lie", "lies", "lied", "lying"), ("die", "dies", "died", "dying"),
        ("stuff", "stuffs", "stuffed", "stuffing"), ("staff", "staffed"),
        ("puff", "puffed"), ("bus", "buses"), ("gas", "gases"), ("ski", "skis"),
        ("kiwi", "kiwis"), ("taxi", "taxis"), ("case", "cases"), ("box", "boxes"),
        ("pie", "pies"), ("cry", "cried"),
    ]
    unmatched = [(form, family[0]) for family in families for form in family[1:]
                 if judge.score(form, family[0]).score != 1.0]
    assert unmatched == []


@pytest.mark.parametrize("answer, reference", [
    ("hop", "hope"), ("car", "care"), ("bed", "bead"), ("ape", "apply"), ("ear", "early"),
])
def test_inflection_rules_keep_distinct_words_apart(answer, reference):
    assert judge.score("making", "make").score == 1.0
    assert judge.score(answer, reference).score == 0.0
