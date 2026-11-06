import unittest

from cogs.auto_responder import _response_match, _sentence_tokens


class AutoResponderMatchingTests(unittest.TestCase):
    def setUp(self):
        self.response = {
            "triggers": "steam\nfree",
            "sub_triggers": "games\ngiveaway",
        }
        self.owners = {"steam": 1, "free": 1, "games": 1, "giveaway": 1}

    def match(self, message):
        return _response_match(self.response, _sentence_tokens(message), self.owners)

    def test_two_primary_terms_in_same_sentence_match(self):
        self.assertIsNotNone(self.match("Steam has free games today."))

    def test_terms_in_separate_sentences_do_not_match(self):
        self.assertIsNone(self.match("Steam is updated. Coffee is free."))

    def test_negated_keyword_rejects_response(self):
        self.assertIsNone(self.match("I don't want free games on Steam."))

    def test_two_variations_can_be_fallback_match(self):
        self.assertIsNotNone(self.match("Games giveaway announced today."))

    def test_single_typo_can_fall_back_when_another_term_matches(self):
        self.assertIsNotNone(self.match("Steem has free games today."))

    def test_adjacent_transposition_is_accepted_as_typo(self):
        self.assertIsNotNone(self.match("Stema has free games today."))

    def test_distant_terms_do_not_match(self):
        self.assertIsNone(self.match("Steam account status is fine and several unrelated words follow before free."))


if __name__ == "__main__":
    unittest.main()
