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


@pytest.mark.parametrize("wrong, right, reference", [
    ("not permanent", "permanent", "permanent"),
    ("permanent", "isn't permanent", "not permanent"),
    ("never ending", "ending", "ending"),
    ("it doesn't last", "it lasts", "lasting"),
    ("hope", "without hope", "lacking hope"),
    ("permanent", "lasting a short time", "not permanent; lasting a short time"),
    ("not existing", "existing", "existing"),
    ("any", "not any", "not any"),       # a definition of stopwords only
])
def test_a_keyword_negated_differently_earns_no_credit(wrong, right, reference):
    rejected = judge.score(wrong, reference)
    assert rejected.score == 0.0 and not rejected.passed
    assert "Negated differently from the definition" in rejected.feedback
    accepted = judge.score(right, reference)
    assert accepted.passed
    assert "Negated differently" not in accepted.feedback


def test_negated_feedback_names_the_word_as_the_definition_writes_it():
    assert judge.score("Permanent", "not Permanent").feedback == (
        "Not accepted — at least 50% of the key terms are needed. "
        "Negated differently from the definition: Permanent.")


VEGAN = "a strict vegetarian; someone who eats no animal or dairy products at all"

# A "not" counts for the next content word only, and its place is compared
# only when the other text has no negative word in that clause. So answers
# that put the negation elsewhere, or say it with another word, keep the
# credit they had; answers that reverse it lose it.
NEGATION_TABLE = [
    # (answer, reference, passes)
    ("unable to read or write", "not able to read or write", True),
    ("can't read or write", "not able to read or write", True),
    ("able to read", "not able to read or write", False),
    ("a vegetarian who avoids animal and dairy products", VEGAN, True),
    ("strict vegetarian who refuses animal or dairy products", VEGAN, True),
    ("strict vegetarian, eats animal", VEGAN, False),
    ("a homeless person who lives on the street",
     "a person without a home who lives on the street", True),
    ("lasting a short time", "not permanent — lasting a short time", True),
    ("lasting a short time", "not permanent - lasting a short time", True),
    ("lasting a short time", "not permanent / lasting a short time", True),
    ("lasting a short time", "not permanent and lasting a short time", True),
    ("permanent, lasting", "not permanent — lasting a short time", False),
    ("doesn't have any money", "having no money", True),
    ("poor, having no money", "having no money", True),
    ("lots of money", "having no money", False),
    ("not in use anymore", "no longer in use", True),
    ("missing school", "absent from school", True),
    ("not at school", "absent from school", True),
    ("at school", "absent from school", False),
    ("sugar-free", "containing no sugar", True),
    ("full of sugar", "containing no sugar", False),
    ("fails to work", "does not work", True),
    ("it works", "does not work", False),
    ("insufficient sleep", "lack of sleep", True),
    ("not enough sleep", "lack of sleep", True),
    ("plenty of sleep", "lack of sleep", False),
    ("not hot or cold", "neither hot nor cold", True),
    ("doesnt last", "not lasting", True),
    ("lasting", "not lasting", False),
    ("without waiting", "without delay", True),
    # a definition that says "not" with a word such as "impossible"
    ("can't forget", "impossible to forget", True),
    ("you can't forget it", "impossible to forget", True),
    ("impossible to forget", "can't forget", True),
    ("cannot be cured", "incapable of being cured", True),
    ("incapable of being cured", "cannot be cured", True),
    ("cannot be avoided or prevented", "incapable of being avoided or prevented", True),
    ("cannot read", "an inability to read", True),
    ("without color", "deprived of color", True),
    ("lacking curiosity", "deficient in curiosity", True),
    ("lacking vitamins", "a deficiency of vitamins", True),
    ("lack of water", "a shortage of water", True),
    ("lack of hearing", "loss of hearing", True),
    ("do not harm", "refrain from harming", True),
    ("do not vote", "abstain from voting", True),
    ("not attend school", "failure to attend (especially school)", True),
    ("not taxed", "exempt from tax", True),
    ("cant be cured", "cannot be cured", True),
    ("wont last", "not lasting", True),
    ("cured", "cannot be cured", False),
    ("lasting", "won't last", False),
]


def test_negation_counts_only_where_one_text_has_none():
    wrong = [(answer, reference, judge.score(answer, reference).score)
             for answer, reference, passes in NEGATION_TABLE
             if judge.score(answer, reference).passed != passes]
    assert wrong == []


def test_a_reversed_keyword_beside_a_matching_one_still_passes():
    # Known limit: "or settled" is not negated, so half the key terms match.
    result = judge.score("fixed and settled", "not fixed or settled")
    assert result.score == 0.5 and result.passed
    assert "Negated differently from the definition: fixed." in result.feedback


def test_contractions_read_as_not():
    assert judge.score("cannot go", "can't go").score == 1.0
    assert judge.score("will not stop", "won't stop").score == 1.0
    assert judge.score("isn\u2019t permanent", "not permanent").score == 1.0
    for answer in ("go", "ca"):                 # no "ca" or "wo" fragments
        result = judge.score(answer, "can't go")
        assert result.score == 0.0 and result.missed == {"cannot"}


@pytest.mark.parametrize("apostrophe", ["´", "`", "ʼ", "‘", "′"])
def test_contractions_typed_with_an_apostrophe_look_alike_read_as_not(apostrophe):
    assert judge.score(f"it doesn{apostrophe}t last", "not lasting").score == 1.0
    assert judge.score(f"doesn{apostrophe}t work", "does not work").score == 1.0
    assert judge.score(f"can{apostrophe}t go", "cannot go").score == 1.0
    assert judge.score(f"won{apostrophe}t stop", "will not stop").score == 1.0
    rejected = judge.score("lasting", f"isn{apostrophe}t lasting")
    assert rejected.score == 0.0
    assert "Negated differently from the definition: lasting." in rejected.feedback


def test_a_space_typed_for_the_apostrophe_reads_as_not():
    assert judge.score("isn t moving", "not moving").score == 1.0
    assert judge.score("it doesn t last", "not lasting").score == 1.0
    assert judge.score("I don t know", "not knowing").score == 1.0
    assert judge.score("can t go", "cannot go").score == 1.0
    assert judge.score("won t stop", "will not stop").score == 1.0
    assert judge.score("moving", "isn t moving").score == 0.0
    # only before a word or the end of a clause, so an initial stays a name
    assert judge.score("Don T. Smith", "Don Smith").score == 1.0
