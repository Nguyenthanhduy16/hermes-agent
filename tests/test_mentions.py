"""Pure offline parsing of confirmed bot names at the start of group text."""

import unicodedata
import unittest

from hermes_zalo.mentions import strip_bot_mention


class MentionTests(unittest.TestCase):
    names = ("Bot LabCoach", "bot.synthetic-handle")

    def test_distinct_display_name_and_handle_both_accept_menu(self):
        for name in self.names:
            with self.subTest(name=name):
                self.assertEqual("/menu", strip_bot_mention(f"@{name} /menu", self.names))

    def test_case_and_unicode_spacing_and_mention_delimiters(self):
        for prefix in ("  @bOt lAbCoAcH ", "\u200b@Bot\u00a0LabCoach\u200b ",
                       "\ufeff@Bot\u202fLabCoach\ufeff ", "@Bot LabCoach: ",
                       "@Bot LabCoach, ", "@Bot LabCoach\t", "@Bot LabCoach\n"):
            with self.subTest(prefix=repr(prefix)):
                self.assertEqual("/menu", strip_bot_mention(prefix + "/menu", self.names))

    def test_canonical_unicode_bot_name_and_body_are_handled_separately(self):
        names = ("Bot Hỗ Trợ",)
        for name in (names[0], unicodedata.normalize("NFD", names[0])):
            self.assertEqual("/menu", strip_bot_mention(f"@{name} /menu", names))
        self.assertEqual("/menu", strip_bot_mention("@Bot Hỗ Trợ /menu", (unicodedata.normalize("NFD", names[0]),)))

    def test_command_arguments_and_natural_question_keep_original_content(self):
        for body in ("/lich homnay | coach=Nguyễn Văn An", "Ngày mai mình có lịch gì?",
                     "/hoi Quy định @Other nói là gì?", "Nội dung có\u200b ký tự và  hai dấu cách",
                     unicodedata.normalize("NFD", "/hoi Nội quy phòng thực hành")):
            self.assertEqual(body, strip_bot_mention("@Bot LabCoach " + body, self.names))

    def test_mention_only_shows_menu(self):
        for text in ("@Bot LabCoach", " @Bot LabCoach \t", "@bot.synthetic-handle:"):
            self.assertEqual("/menu", strip_bot_mention(text, self.names))

    def test_unknown_middle_and_prefix_collision_mentions_stay_unchanged(self):
        for text in ("@Other /menu", "@Bot LabCoachElse /menu", "@Bot LabCoach/ menu",
                     "@bot.synthetic-handle-extra /menu", "Hãy đọc @Bot LabCoach /menu",
                     "/hoi Nội dung @Bot LabCoach", "Bot LabCoach /menu", "/menu",
                     "@LabCoach /menu", "@all /menu"):
            self.assertEqual(text, strip_bot_mention(text, self.names))

    def test_names_are_literal_and_longest_matching_name_wins(self):
        self.assertEqual("/menu", strip_bot_mention("@Bot [Demo]+ /menu", ("Bot [Demo]+",)))
        text = "@Bot D /menu"
        self.assertEqual(text, strip_bot_mention(text, ("Bot [Demo]+",)))
        self.assertEqual("/menu", strip_bot_mention("@Bot LabCoach /menu", ("Bot", "Bot LabCoach")))

    def test_missing_names_never_strip_an_arbitrary_mention(self):
        text = "@Bot LabCoach /menu"
        self.assertEqual(text, strip_bot_mention(text, ()))
        self.assertEqual(text, strip_bot_mention(text, ("", " ")))


if __name__ == "__main__":
    unittest.main()
