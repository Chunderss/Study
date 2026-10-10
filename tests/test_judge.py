"""KeywordJudge: how a typed definition is graded in mechanical study."""
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
