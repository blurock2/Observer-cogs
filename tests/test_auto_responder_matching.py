import unittest

from cogs.auto_responder import _next_allowed_at, _response_match, _sentence_tokens


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
        self.assertIsNotNone(self.match("Steem has free."))

    def test_adjacent_transposition_is_accepted_as_typo(self):
        self.assertIsNotNone(self.match("Stema has free."))

    def test_two_distinct_misspellings_can_match(self):
        self.assertIsNotNone(self.match("Steem has frea."))

    def test_one_typo_cannot_satisfy_two_keywords(self):
        response = {"triggers": "steam\nstream", "sub_triggers": ""}
        self.assertIsNone(_response_match(response, _sentence_tokens("steem"), {"steam": 1, "stream": 1}))

    def test_not_only_is_not_treated_as_negation(self):
        self.assertIsNotNone(self.match("Not only Steam is free."))

    def test_common_internet_slang_variations(self):
        response = {"triggers": "free\ngames", "sub_triggers": "fr\nf2p\ngamez\ng4mes\nggs"}
        owners = {word: 1 for word in ("free", "games", "fr", "f2p", "gamez", "g4mes", "ggs")}
        for message in ("fr games", "f2p games", "free gamez", "free g4mes", "free ggs"):
            with self.subTest(message=message):
                self.assertIsNotNone(_response_match(response, _sentence_tokens(message), owners))

    def test_slang_requires_configured_variation(self):
        self.assertIsNone(self.match("Steam fr"))

    def test_common_chat_negations_reject(self):
        for message in ("Nah, free games on Steam", "Nope, free games on Steam", "I won't use free games on Steam"):
            with self.subTest(message=message):
                self.assertIsNone(self.match(message))

    def test_unrelated_sentence_after_keyword_does_not_match(self):
        self.assertIsNone(self.match("Steam is fine. Anyway, free coffee is cheap."))

    def test_unicode_and_mixed_case(self):
        self.assertIsNotNone(self.match("STEAM has FREE games today"))

    def test_distant_terms_do_not_match(self):
        self.assertIsNone(self.match("Steam account status is fine and several unrelated words follow before the weather report says the coffee is finally free."))

    def test_required_context_blocks_the_observer_bot_false_positive(self):
        response = {
            "triggers": "Observer\nSafe\nScam\nSpyware\nTrustworthy",
            "sub_triggers": "Scammy\nScam\nBot\nthis",
            "required_groups": "safe, scam, scammy, spyware, trustworthy",
        }
        owners = {word.casefold(): 1 for word in (
            "Observer", "Safe", "Scam", "Spyware", "Trustworthy",
            "Scammy", "Bot", "this",
        )}
        self.assertIsNone(_response_match(
            response, _sentence_tokens("I think someone set up a bot for Observer"), owners
        ))

    def test_negation_across_natural_phrase_is_rejected(self):
        response = {
            "triggers": "Observer\\nSafe\\nScam\\nSpyware\\nTrustworthy",
            "sub_triggers": "Scammy\\nBot\\nthis",
            "required_groups": "safe, scam, scammy, spyware, trustworthy",
        }
        owners = {word.casefold(): 1 for word in (
            "Observer", "Safe", "Scam", "Spyware", "Trustworthy", "Scammy", "Bot", "this"
        )}
        self.assertIsNone(_response_match(
            response, _sentence_tokens("I don't think the Observer bot is safe."), owners
        ))

    def test_required_context_allows_relevant_safety_question(self):
        response = {
            "triggers": "Observer\nSafe\nScam\nSpyware\nTrustworthy",
            "sub_triggers": "Scammy\nBot",
            "required_groups": "safe, scam, scammy, spyware, trustworthy",
        }
        owners = {word.casefold(): 1 for word in (
            "Observer", "Safe", "Scam", "Spyware", "Trustworthy", "Scammy", "Bot"
        )}
        self.assertIsNotNone(_response_match(
            response, _sentence_tokens("Is the Observer bot safe?"), owners
        ))

    def test_each_required_group_must_match(self):
        response = {
            "triggers": "Observer\nBot",
            "sub_triggers": "",
            "required_groups": "safe, trustworthy\nscam, spyware",
        }
        owners = {"observer": 1, "bot": 1}
        self.assertIsNone(_response_match(
            response, _sentence_tokens("Is the Observer bot safe?"), owners
        ))
        self.assertIsNotNone(_response_match(
            response, _sentence_tokens("Is the Observer bot safe or spyware?"), owners
        ))

    def test_comma_separated_context_terms_are_alternatives(self):
        response = {
            "triggers": "Observer\nBot",
            "sub_triggers": "",
            "required_groups": "safe, trustworthy",
        }
        owners = {"observer": 1, "bot": 1}
        self.assertIsNotNone(_response_match(
            response, _sentence_tokens("Is the Observer bot trustworthy?"), owners
        ))

    def test_fuzzy_match_is_marked_uncertain_and_doubles_cooldown(self):
        match = self.match("Steem has free.")
        self.assertIsNotNone(match)
        self.assertTrue(match[2])
        self.assertEqual(_next_allowed_at(100.0, 60, True), 220.0)
        self.assertEqual(_next_allowed_at(100.0, 60, False), 160.0)

    def test_exact_match_keeps_normal_cooldown(self):
        self.assertEqual(_next_allowed_at(100.0, 60, False), 160.0)


if __name__ == "__main__":
    unittest.main()
