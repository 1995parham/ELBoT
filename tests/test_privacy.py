"""Profile birthdays and privacy rules.

The rule list is order-sensitive — Telegram applies the first rule that
matches — so the exceptions have to come before the base rule, or "contacts
except X" quietly becomes "contacts". That and the parsing are pure functions,
checked here without a network.
"""

import pytest
from telethon.tl.types import (
    Birthday,
    InputPrivacyValueAllowAll,
    InputPrivacyValueAllowContacts,
    InputPrivacyValueAllowUsers,
    InputPrivacyValueDisallowAll,
    InputPrivacyValueDisallowChatParticipants,
    InputPrivacyValueDisallowUsers,
    InputUser,
    PrivacyValueAllowContacts,
    PrivacyValueAllowUsers,
    PrivacyValueDisallowAll,
    PrivacyValueDisallowUsers,
)

import topoli_user as t


class TestBirthday:
    def test_day_first_with_or_without_a_year(self):
        assert t.parse_birthday("27-01-1995") == Birthday(day=27, month=1, year=1995)
        assert t.parse_birthday("7-3") == Birthday(day=7, month=3, year=None)

    def test_none_clears_it(self):
        assert t.parse_birthday("none") is None

    def test_a_day_that_does_not_exist_is_refused(self):
        with pytest.raises(SystemExit):
            t.parse_birthday("31-02")
        with pytest.raises(SystemExit):
            t.parse_birthday("1995-01-27")

    def test_29_february_needs_no_year(self):
        assert t.parse_birthday("29-02").day == 29

    def test_text_round_trip(self):
        assert t.birthday_text(t.parse_birthday("27-01-1995")) == "27-01-1995"
        assert t.birthday_text(t.parse_birthday("27-01")) == "27-01"


class TestRules:
    def test_the_base_rule_alone(self):
        assert [type(r) for r in t.privacy_rules("nobody")] == [InputPrivacyValueDisallowAll]
        assert [type(r) for r in t.privacy_rules("everybody")] == [InputPrivacyValueAllowAll]

    def test_exceptions_come_before_the_base_rule(self):
        user = InputUser(user_id=1, access_hash=2)
        rules = t.privacy_rules("contacts", allow_users=[], deny_users=[user], deny_chats=[9])
        assert [type(r) for r in rules] == [
            InputPrivacyValueDisallowUsers,
            InputPrivacyValueDisallowChatParticipants,
            InputPrivacyValueAllowContacts,
        ]
        rules = t.privacy_rules("nobody", allow_users=[user])
        assert [type(r) for r in rules] == [InputPrivacyValueAllowUsers, InputPrivacyValueDisallowAll]

    def test_an_exception_that_repeats_the_base_is_refused(self):
        user = InputUser(user_id=1, access_hash=2)
        with pytest.raises(SystemExit):
            t.privacy_rules("everybody", allow_users=[user])
        with pytest.raises(SystemExit):
            t.privacy_rules("nobody", deny_users=[user])

    def test_reading_rules_back_names_the_people(self):
        rules = [PrivacyValueDisallowUsers(users=[5]), PrivacyValueAllowContacts()]
        d = t.describe_rules(rules, {5: "Ali"})
        assert d == {"rule": "contacts", "allow": [], "deny": ["Ali"]}
        assert t.rules_text(d) == "contacts, except Ali"

    def test_the_first_base_rule_wins(self):
        """Telegram returns "contacts" as AllowContacts then DisallowAll."""
        d = t.describe_rules([PrivacyValueAllowContacts(), PrivacyValueDisallowAll()])
        assert d["rule"] == "contacts"

    def test_no_base_rule_reads_as_nobody(self):
        d = t.describe_rules([PrivacyValueAllowUsers(users=[5])])
        assert d == {"rule": "nobody", "allow": ["5"], "deny": []}
        assert t.describe_rules([PrivacyValueDisallowAll()])["rule"] == "nobody"

    def test_every_setting_has_a_key(self):
        assert {"phone", "last-seen", "photo", "bio", "birthday", "calls", "forwards"} <= set(t.PRIVACY_KEYS)
