import unittest

from cogs.mentions import Mentions


class FakeRole:
    def __init__(self, role_id, name="role"):
        self.id = role_id
        self.name = name


class FakeMember:
    def __init__(self, roles, mention="@member"):
        self.roles = roles
        self.mention = mention


class FakeMessage:
    def __init__(self, author, mentions=None, role_mentions=None):
        self.author = author
        self.mentions = mentions or []
        self.role_mentions = role_mentions or []


class MentionProtectionTests(unittest.TestCase):
    blocked_role_id = 10
    allow_role_id = 20

    def setUp(self):
        self.cog = object.__new__(Mentions)
        self.protected_member = FakeMember(
            [FakeRole(self.blocked_role_id)],
            mention="@protected",
        )

    def get_reason(self, author, mentions=None, role_mentions=None):
        message = FakeMessage(author, mentions, role_mentions)
        return self.cog._get_violation_reason(
            message,
            self.blocked_role_id,
            self.allow_role_id,
        )

    def test_protected_member_can_ping_protected_member(self):
        author = FakeMember([FakeRole(self.blocked_role_id)])

        self.assertIsNone(self.get_reason(author, [self.protected_member]))

    def test_allow_pings_member_can_ping_protected_member(self):
        author = FakeMember([FakeRole(self.allow_role_id)])

        self.assertIsNone(self.get_reason(author, [self.protected_member]))

    def test_member_without_permission_cannot_ping_protected_member(self):
        author = FakeMember([])

        self.assertEqual(
            self.get_reason(author, [self.protected_member]),
            "pinging protected member @protected",
        )

    def test_protected_member_with_allow_role_can_be_pinged(self):
        author = FakeMember([])
        target = FakeMember(
            [FakeRole(self.blocked_role_id), FakeRole(self.allow_role_id)],
            mention="@protected",
        )

        self.assertIsNone(self.get_reason(author, [target]))

    def test_protected_member_without_allow_role_still_cannot_be_pinged(self):
        author = FakeMember([])

        self.assertEqual(
            self.get_reason(author, [self.protected_member]),
            "pinging protected member @protected",
        )

    def test_member_with_permission_can_ping_protected_role(self):
        author = FakeMember([FakeRole(self.allow_role_id)])
        protected_role = FakeRole(self.blocked_role_id, name="Protected")

        self.assertIsNone(self.get_reason(author, role_mentions=[protected_role]))


if __name__ == "__main__":
    unittest.main()