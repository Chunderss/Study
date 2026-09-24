import io
import os
import tempfile
import time
import unittest
from pathlib import Path

from vocab.algorithms.base import Review, get_scheduler
from vocab.algorithms.leitner import _INTERVALS, _DAY
from vocab.cli.app import App
from vocab.cli.parser import parse
from vocab.cli.repl import Repl
from vocab.core.models import Sense, Word, WordList
from vocab.core.paths import InvalidListName, sanitize_list_name
from vocab.core.storage import ListExists, ListNotFound, Storage
from vocab.dictionaries import REGISTRY
from vocab.dictionaries.base import Dictionary, LookupFailed
from vocab.study.judge import KeywordJudge


class MockDictionary(Dictionary):
    id = "mock"
    name = "Mock"
    description = "test double"
    install_cost = ""
    requires_network = False

    def __init__(self, table):
        self.table = table

    def available(self):
        return True

    def install(self):
        pass

    def lookup(self, word):
        w = word.strip().lower()
        if w in self.table:
            return [Sense(definition=self.table[w], pos="noun")]
        raise LookupFailed(f"'{word}' not found.", recoverable=False)


def make_app():
    """App wired to a temp home + a mock dictionary."""
    tmp = tempfile.mkdtemp(prefix="vocabtest_")
    app = App(root=tmp)
    REGISTRY["mock"] = MockDictionary({
        "ephemeral": "lasting for a very short time",
        "gatsby": "a wealthy mysterious host of lavish parties",
        "verdant": "green with vegetation",
    })
    app.config.active_dictionary = "mock"
    app.config.save(app.paths)
    return app, tmp


class TestPaths(unittest.TestCase):
    def test_sanitize_ok(self):
        self.assertEqual(sanitize_list_name("The Great Gatsby"), "The Great Gatsby")

    def test_sanitize_rejects_traversal(self):
        for bad in ["../etc", "a/b", "con", "", "x" * 200, "nul", "STUDYALL"]:
            with self.assertRaises(InvalidListName):
                sanitize_list_name(bad)


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.app, _ = make_app()
        self.s = self.app.storage

    def test_create_list_and_duplicate(self):
        self.s.create("Gatsby")
        self.assertIn("Gatsby", self.s.list_names())
        with self.assertRaises(ListExists):
            self.s.create("Gatsby")

    def test_delete_missing(self):
        with self.assertRaises(ListNotFound):
            self.s.delete("Nope")

    def test_word_roundtrip(self):
        self.s.create("L")
        wl = self.s.load_words("L")
        wl.add(Word(word="Verdant", dictionary="mock",
                    senses=[Sense(definition="green")]))
        self.s.save_words(wl)
        again = self.s.load_words("L")
        self.assertTrue(again.has("verdant"))          # case-insensitive key
        self.assertEqual(again.words["verdant"].primary_definition(), "green")

    def test_export_import_fresh_progress(self):
        self.app.cmd_create("Src")
        self.app.cmd_use("Src")
        self.app.cmd_add("ephemeral")
        dest = Path(self.app.paths.root) / "bundle.json"
        self.app.cmd_export("Src", str(dest), include_stats=True)
        # import under a new name, WITHOUT stats -> fresh progress
        self.app.cmd_import(str(dest), "Copy", with_stats=False)
        stats = self.s.load_stats("Copy")
        # fresh import: stats file exists but empty (progress starts on first study)
        self.assertEqual(stats, {})
        self.assertTrue(self.s.load_words("Copy").has("ephemeral"))


class TestLeitner(unittest.TestCase):
    def setUp(self):
        self.sched = get_scheduler("leitner")

    def test_promote_and_demote(self):
        now = 1_000_000.0
        c = self.sched.new_card()
        self.assertTrue(self.sched.is_due(c, now))     # box1 due immediately
        c = self.sched.review(c, Review.GOOD, now)
        self.assertEqual(c["box"], 2)
        self.assertAlmostEqual(c["due"], now + _INTERVALS[2] * _DAY)
        self.assertFalse(self.sched.is_due(c, now))    # not due until interval
        # a wrong answer sends it back to box 1
        c = self.sched.review(c, Review.AGAIN, now)
        self.assertEqual(c["box"], 1)
        self.assertEqual(c["lapses"], 1)

    def test_due_order_weakest_first(self):
        now = 2_000_000.0
        cards = {
            "a": {"box": 3, "due": 0},
            "b": {"box": 1, "due": 0},
            "c": {"box": 2, "due": 0},
        }
        self.assertEqual(self.sched.due_order(cards, now), ["b", "c", "a"])


class TestJudge(unittest.TestCase):
    def test_keyword_overlap(self):
        j = KeywordJudge()
        ref = "lasting for a very short time"
        good = j.score("something that lasts a short time", ref)
        bad = j.score("a kind of bird", ref)
        self.assertGreater(good.score, bad.score)
        self.assertGreaterEqual(good.score, 0.5)


class TestParser(unittest.TestCase):
    def test_quoted_name(self):
        c = parse('CREATE "The Great Gatsby"')
        self.assertEqual(c.verb, "CREATE")
        self.assertEqual(c.args, ["The Great Gatsby"])

    def test_manual_def_argstr(self):
        c = parse("ADD ephemeral :: lasting a short time")
        self.assertEqual(c.verb, "ADD")


class TestAppFlow(unittest.TestCase):
    def setUp(self):
        self.app, _ = make_app()

    def test_add_requires_list(self):
        with self.assertRaises(ValueError):
            self.app.cmd_add("ephemeral")   # no current list

    def test_full_add_and_studyall_writeback(self):
        # two lists, one word each
        self.app.cmd_create("A")
        self.app.cmd_create("B")
        self.app.cmd_add("ephemeral", target="A")
        self.app.cmd_add("verdant", target="B")

        # scripted study: reveal mode reads TWO prompts per card
        # (reveal -> Enter, then verdict -> 'y'). Interleave accordingly.
        answers = iter(["", "y", "", "y", "", "y", "", "y"])
        def prompt(_):
            return next(answers)
        echoes = []
        out = self.app.run_study(self.app.study_targets_all(), prompt, echoes.append,
                                 session_label="ALL")
        self.assertIn("correct", out)
        # progress must be written back to the HOME lists, not a mixed dir
        self.assertNotIn("STUDYALL", self.app.storage.list_names())
        stats_a = self.app.storage.load_stats("A")
        self.assertEqual(stats_a["ephemeral"]["box"], 2)   # promoted


class TestReplDispatch(unittest.TestCase):
    def _run(self, script: str):
        app, _ = make_app()
        inp = io.StringIO(script)
        out = io.StringIO()
        Repl(app, in_stream=inp, out_stream=out).loop()
        return out.getvalue(), app

    def test_scripted_session(self):
        script = "\n".join([
            "CREATE Gatsby",
            "USE Gatsby",
            "ADD ephemeral",
            "LS",
            "STATS",
            "QUIT",
        ]) + "\n"
        out, app = self._run(script)
        self.assertIn("Created list 'Gatsby'", out)
        self.assertIn("Now using 'Gatsby'", out)
        self.assertIn("ephemeral", out)
        self.assertTrue(app.storage.load_words("Gatsby").has("ephemeral"))

    def test_unknown_command_survives(self):
        out, _ = self._run("FLOOP\nQUIT\n")
        self.assertIn("Unknown command 'FLOOP'", out)

    def test_addl_with_manual_definition(self):
        # regression: ADDL must honour the '::' manual-definition syntax the
        # same way ADD does (originally it ignored it and hit the network).
        script = "\n".join([
            'CREATE "Moby Dick"',
            'ADDL "Moby Dick" leviathan :: a very large aquatic creature',
            'LS "Moby Dick"',
            "QUIT",
        ]) + "\n"
        out, app = self._run(script)
        wl = app.storage.load_words("Moby Dick")
        self.assertTrue(wl.has("leviathan"))
        self.assertEqual(wl.words["leviathan"].dictionary, "manual")
        self.assertIn("aquatic", wl.words["leviathan"].primary_definition())


class TestDictionaryDefaultAndFallback(unittest.TestCase):
    def test_default_is_wordnet(self):
        import tempfile
        from vocab.core.config import Config
        # A brand-new config (fresh home) must prefer the offline default.
        cfg = Config()
        self.assertEqual(cfg.active_dictionary, "wordnet")

    def test_fallback_when_preferred_unavailable(self):
        import tempfile
        from vocab.dictionaries.wordnet import WordNet
        app = App(root=tempfile.mkdtemp(prefix="vocabwn_"))
        app.config.active_dictionary = "wordnet"
        orig = WordNet.available
        WordNet.available = lambda self: False   # pretend corpus missing
        try:
            dic, notice = app.effective_dictionary()
            self.assertNotEqual(dic.id, "wordnet")   # fell back
            self.assertIn("dict install wordnet", notice)
        finally:
            WordNet.available = orig

    def test_no_notice_when_preferred_available(self):
        import tempfile
        from vocab.dictionaries.wordnet import WordNet
        app = App(root=tempfile.mkdtemp(prefix="vocabwn2_"))
        app.config.active_dictionary = "wordnet"
        orig = WordNet.available
        WordNet.available = lambda self: True
        # patch lookup so we don't require the real corpus here
        orig_lu = WordNet.lookup
        WordNet.lookup = lambda self, w: [Sense(definition="x")]
        try:
            dic, notice = app.effective_dictionary()
            self.assertEqual(dic.id, "wordnet")
            self.assertEqual(notice, "")
        finally:
            WordNet.available = orig
            WordNet.lookup = orig_lu


@unittest.skipUnless(
    __import__("vocab.dictionaries.wordnet", fromlist=["WordNet"]).WordNet().available(),
    "WordNet corpus not installed on this machine",
)
class TestWordNetLive(unittest.TestCase):
    """Runs only where the corpus exists; verifies real offline lookups."""
    def test_real_lookup_multiple_senses(self):
        from vocab.dictionaries.wordnet import WordNet
        senses = WordNet().lookup("run")
        self.assertGreater(len(senses), 1)
        self.assertTrue(all(s.definition for s in senses))

    def test_not_found_is_unrecoverable(self):
        from vocab.dictionaries.wordnet import WordNet
        from vocab.dictionaries.base import LookupFailed
        with self.assertRaises(LookupFailed) as cm:
            WordNet().lookup("asdfqwerzxcvzzz")
        self.assertFalse(cm.exception.recoverable)


class TestDoctor(unittest.TestCase):
    def test_doctor_reports_key_fields(self):
        import tempfile
        app = App(root=tempfile.mkdtemp(prefix="vocabdoc_"))
        out = app.cmd_doctor()
        for field in ["Python", "nltk", "WordNet data", "Configured", "Effective"]:
            self.assertIn(field, out)

    def test_doctor_flags_online_fallback(self):
        import tempfile
        from vocab.dictionaries.wordnet import WordNet
        app = App(root=tempfile.mkdtemp(prefix="vocabdoc2_"))
        app.config.active_dictionary = "wordnet"
        orig = WordNet.available
        WordNet.available = lambda self: False   # force fallback to online
        try:
            out = app.cmd_doctor()
            # verdict must warn about the online/timeout situation, not claim OK
            self.assertIn("PROBLEM", out)
            self.assertNotIn("OK: using the offline dictionary", out)
        finally:
            WordNet.available = orig


class TestStudySession(unittest.TestCase):
    """The non-blocking controller both the terminal and GUI drive."""
    def _app_with_two_lists(self):
        app, _ = make_app()
        app.cmd_create("A")
        app.cmd_create("B")
        app.cmd_add("ephemeral", target="A")
        app.cmd_add("verdant", target="B")
        return app

    def test_reveal_flow_and_writeback(self):
        from vocab.study.session import StudySession
        app = self._app_with_two_lists()
        s = StudySession(app.storage, app.scheduler,
                         app.study_targets_all(), session_label="ALL")
        self.assertEqual(s.total, 2)
        seen = []
        while not s.done:
            view = s.current()
            self.assertIsNotNone(view)
            seen.append(view.word)
            outcome = s.answer(True)          # got it -> promote
            self.assertEqual(outcome.box_before, 1)
            self.assertEqual(outcome.box_after, 2)
            self.assertTrue(outcome.correct)
        self.assertEqual(len(seen), 2)
        summary = s.finish()
        self.assertIn("2/2", summary)
        # progress written back to HOME lists, no mixed list created
        self.assertNotIn("STUDYALL", app.storage.list_names())
        self.assertEqual(app.storage.load_stats("A")["ephemeral"]["box"], 2)
        self.assertEqual(app.storage.load_stats("B")["verdant"]["box"], 2)

    def test_wrong_answer_demotes(self):
        from vocab.study.session import StudySession
        app = self._app_with_two_lists()
        # promote A/ephemeral once so it's above box 1
        s1 = StudySession(app.storage, app.scheduler, ["A"])
        s1.answer(True); s1.finish()
        self.assertEqual(app.storage.load_stats("A")["ephemeral"]["box"], 2)
        # now a wrong answer should send it back to box 1
        # (force due by clearing the due time)
        stats = app.storage.load_stats("A"); stats["ephemeral"]["due"] = 0.0
        app.storage.save_stats("A", stats)
        s2 = StudySession(app.storage, app.scheduler, ["A"])
        out = s2.answer(False); s2.finish()
        self.assertEqual(out.box_after, 1)
        self.assertFalse(out.correct)

    def test_mechanical_mode_scoring(self):
        from vocab.study.session import StudySession
        from vocab.study.judge import KeywordJudge
        app = self._app_with_two_lists()
        s = StudySession(app.storage, app.scheduler, ["A"],
                         mechanical=True, judge=KeywordJudge())
        out = s.answer_text("something that lasts a very short time")
        self.assertIsNotNone(out.score)
        self.assertTrue(out.correct)          # good overlap
        s.finish()


class TestModulesAndNotes(unittest.TestCase):
    def test_module_dirs_created(self):
        app, tmp = make_app()
        app.cmd_create("Gatsby")
        p = app.paths
        self.assertTrue(p.manifest_file("Gatsby").exists(), "module.json missing")
        self.assertTrue(p.vocab_dir("Gatsby").exists(), "vocab/ missing")
        self.assertTrue(p.notes_dir("Gatsby").exists(), "notes/ missing")
        self.assertTrue(p.documents_dir("Gatsby").exists(), "documents/ missing")
        # words live under vocab/
        self.assertIn("modules", str(p.words_file("Gatsby")).replace("\\", "/"))
        self.assertIn("vocab", str(p.words_file("Gatsby")).replace("\\", "/"))

    def test_notes_crud(self):
        app, _ = make_app()
        app.cmd_create("N")
        app.cmd_use("N")
        self.assertEqual(app.storage.note_names("N"), [])
        app.cmd_note_add("chapter1", "# Chapter 1\n\nGatsby throws parties.")
        self.assertEqual(app.storage.note_names("N"), ["chapter1"])
        body = app.cmd_note_view("chapter1")
        self.assertIn("throws parties", body)
        # overwrite
        app.cmd_note_add("chapter1", "updated body")
        self.assertEqual(app.cmd_note_view("chapter1"), "updated body")
        app.cmd_note_delete("chapter1")
        self.assertEqual(app.storage.note_names("N"), [])

    def test_note_name_sanitized(self):
        app, _ = make_app()
        app.cmd_create("N")
        with self.assertRaises(Exception):
            app.storage.save_note("N", "../evil", "x")

    def test_reading_position_roundtrip(self):
        app, _ = make_app()
        app.cmd_create("Book")
        # nothing saved yet -> empty dict
        self.assertEqual(app.storage.load_reading_pos("Book", "gatsby.epub"), {})
        app.storage.save_reading_pos("Book", "gatsby.epub",
                                     {"chapter": 3, "scroll": 0.42, "zoom": 1.6})
        got = app.storage.load_reading_pos("Book", "gatsby.epub")
        self.assertEqual(got, {"chapter": 3, "scroll": 0.42, "zoom": 1.6})
        # positions are keyed per file
        app.storage.save_reading_pos("Book", "other.pdf", {"page": 12, "zoom": 1.0})
        self.assertEqual(app.storage.load_reading_pos("Book", "gatsby.epub")["chapter"], 3)
        self.assertEqual(app.storage.load_reading_pos("Book", "other.pdf")["page"], 12)

    def test_list_aliases_still_work(self):
        # LIST/LISTS terminology preserved as aliases over modules
        app, _ = make_app()
        app.cmd_create("A")
        self.assertIn("A", app.storage.list_names())
        self.assertEqual(app.storage.list_names(), app.storage.module_names())

    def test_disambiguator_pos_filter(self):
        # POS filtering floats the adjective sense of 'ephemeral' over the
        # (frequency-first) mayfly noun sense when used adjectivally.
        from vocab.study.disambiguate import get_disambiguator
        dis = get_disambiguator("nlp")
        senses = [
            Sense(definition="an insect that lives only a day", pos="noun"),
            Sense(definition="lasting a very short time", pos="adjective"),
        ]
        ranked = dis.rank("ephemeral", "the ephemeral joys of childhood", senses)
        # Only assert the reordering when the POS tagger is actually available;
        # without it (no corpus/network) rank() is a safe no-op by design.
        if dis._wn_pos_of("ephemeral", "the ephemeral joys of childhood") == "a":
            self.assertEqual(ranked[0].pos, "adjective")
        # safe no-ops regardless of tagger
        self.assertEqual(dis.rank("x", "", senses), senses)
        self.assertEqual(dis.rank("x", "a sentence", senses[:1]), senses[:1])

    def test_disambiguator_unknown_falls_back_to_nlp(self):
        from vocab.study.disambiguate import get_disambiguator, NlpDisambiguator
        self.assertIsInstance(get_disambiguator("nonsense"), NlpDisambiguator)

    def test_cmd_disambig_switch_and_persist(self):
        app, _ = make_app()
        # default engine reported
        self.assertIn("active : nlp", app.cmd_disambig(""))
        # switch persists to config
        app.cmd_disambig("base")
        self.assertEqual(app.config.disambiguator, "base")
        from vocab.core.config import Config
        self.assertEqual(Config.load(app.paths).disambiguator, "base")
        # invalid rejected, engine unchanged
        msg = app.cmd_disambig("bogus")
        self.assertIn("Unknown engine", msg)
        self.assertEqual(app.config.disambiguator, "base")
        # status dict shape
        st = app.disambig_status()
        self.assertEqual(set(st), {"engine", "model", "ollama_up"})


class TestMigration(unittest.TestCase):
    def test_legacy_lists_migrated_with_backup(self):
        import json as _json
        import os
        from vocab.core.paths import get_paths
        from vocab.core import migrate as mig
        from vocab.core.storage import Storage

        tmp = tempfile.mkdtemp(prefix="vocabmig_")
        # Hand-build a legacy lists/ layout (pre-module)
        legacy = os.path.join(tmp, "lists", "The Great Gatsby")
        os.makedirs(os.path.join(legacy, "sources"))
        with open(os.path.join(legacy, "words.json"), "w", encoding="utf-8") as f:
            _json.dump({"schema": 1, "name": "The Great Gatsby",
                        "words": {"ephemeral": {"dictionary": "manual", "added": "x",
                                                "senses": [{"definition": "brief", "pos": "", "example": ""}]}}}, f)
        with open(os.path.join(legacy, "stats.json"), "w", encoding="utf-8") as f:
            _json.dump({"ephemeral": {"algo": "leitner", "box": 3, "due": 0.0,
                                      "reps": 5, "lapses": 1, "last": 0.0}}, f)
        with open(os.path.join(legacy, "sources", "note.txt"), "w") as f:
            f.write("ref")

        paths = get_paths(tmp)
        self.assertTrue(mig.needs_migration(paths))
        created = mig.migrate(paths)
        self.assertEqual(created, ["The Great Gatsby"])

        # words + PROGRESS preserved (box 3 must survive)
        s = Storage(paths)
        wl = s.load_words("The Great Gatsby")
        self.assertTrue(wl.has("ephemeral"))
        self.assertEqual(s.load_stats("The Great Gatsby")["ephemeral"]["box"], 3)
        # legacy sources copied into documents/
        self.assertIn("note.txt", s.document_names("The Great Gatsby"))
        # a backup of the original lists/ exists
        backups = [d for d in os.listdir(tmp) if d.startswith("lists.backup-")]
        self.assertTrue(backups, "no backup of legacy lists/ was made")
        # idempotent: running again is a no-op
        self.assertFalse(mig.needs_migration(paths))
        self.assertEqual(mig.migrate(paths), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
