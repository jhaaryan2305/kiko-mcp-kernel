import unittest

from kernel.redactor import PIIRedactor


class RedactorTest(unittest.TestCase):
    def test_redact_and_rehydrate_round_trip(self) -> None:
        r = PIIRedactor(known_names=["Priya", "Priya Sharma"])
        text = "Email priya@example.com and tell Priya Sharma, call +91 98765 43210"
        out = r.redact(text)
        for secret in ("priya@example.com", "Priya Sharma", "98765"):
            self.assertNotIn(secret, out.redacted)
        self.assertIn("[CONTACT_1]", out.redacted)
        self.assertEqual(PIIRedactor.rehydrate(out.redacted, out.token_map), text)

    def test_same_value_reuses_placeholder(self) -> None:
        out = PIIRedactor(["Priya"]).redact("Priya said hi to Priya")
        self.assertEqual(out.redacted, "[CONTACT_1] said hi to [CONTACT_1]")

    def test_rehydrate_nested_arguments(self) -> None:
        args = {"to": "[CONTACT_1]", "cc": ["[EMAIL_1]"], "n": 3}
        tokens = {"[CONTACT_1]": "Priya", "[EMAIL_1]": "p@example.com"}
        self.assertEqual(PIIRedactor.rehydrate(args, tokens), {"to": "Priya", "cc": ["p@example.com"], "n": 3})


if __name__ == "__main__":
    unittest.main()
