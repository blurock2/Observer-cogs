import unittest

from cogs.security import detect_scam_signals


class SecurityDetectorTests(unittest.TestCase):
    def test_detects_nitro_shortener_and_lookalike_signals(self):
        signals = detect_scam_signals(
            "Free Nitro: verify your account at https://disc0rd-gift.example"
        )

        self.assertIn("free nitro", signals)
        self.assertTrue(any(signal.startswith("lookalike domain:") for signal in signals))

        shortener_signals = detect_scam_signals("https://bit.ly/discord-gift")
        self.assertIn("shortened link: bit.ly", shortener_signals)

    def test_normal_message_has_no_scam_signals(self):
        self.assertEqual(detect_scam_signals("Please read the server rules."), [])

    def test_detects_obfuscated_and_deceptive_urls(self):
        cases = {
            "https://127.0.0.1/login": "raw IP address link:",
            "https://trusted.example@evil.example/login": "URL contains misleading credentials",
            "https://xn--discord-gift.example/login": "punycode domain:",
            "https://discord.com.login.evil.example/verify": "brand impersonation domain:",
            "https://evil.example/download.zip": "suspicious download link:",
            "Please verify at https://evil.example/oauth/authorize?redirect=https://x.example": (
                "suspicious authorization link:"
            ),
        }

        for content, expected in cases.items():
            with self.subTest(content=content):
                self.assertTrue(
                    any(signal == expected or signal.startswith(expected) for signal in detect_scam_signals(content)),
                )

    def test_detects_qr_and_encoded_credential_bait(self):
        qr_signals = detect_scam_signals("Scan this QR code to claim your reward")
        self.assertIn("QR-code phishing bait", qr_signals)

        encoded_signals = detect_scam_signals(
            "Login here: https://evil.example/?next=%2Fverify%3Ftoken%3Dabc"
        )
        self.assertIn("encoded credential bait: evil.example", encoded_signals)

    def test_normal_url_has_no_url_shape_signal(self):
        self.assertEqual(detect_scam_signals("https://example.com/docs"), [])

    def test_detects_delivery_and_invoice_scam_context(self):
        delivery = detect_scam_signals(
            "Your package is waiting for delivery confirmation. "
            "Update your address at https://parcel-tracking.example/confirm"
        )
        self.assertIn("package waiting", delivery)
        self.assertTrue(any(signal.startswith("suspicious scam path:") for signal in delivery))

        invoice = detect_scam_signals(
            "Invoice overdue. Download the payment document at "
            "https://billing.example/invoice.html"
        )
        self.assertIn("invoice overdue", invoice)
        self.assertTrue(any(signal.startswith("suspicious scam webpage:") for signal in invoice))

    def test_generic_html_link_remains_benign(self):
        self.assertEqual(detect_scam_signals("Read https://example.com/docs.html"), [])

    def test_legitimate_brand_links_and_ordinary_discussion_are_not_flagged(self):
        for content in (
            "https://discord.com/developers/docs",
            "https://support.discord.com/hc/en-us",
            "https://steamcommunity.com/id/example",
            "Password reset: https://accounts.google.com/password/reset",
            "I need help with my token and webhook",
            "How do I report free nitro scams to discord support?",
            "Scan this QR code for the menu",
            "https://discord.com/channels/1/2/3",
        ):
            with self.subTest(content=content):
                self.assertEqual(detect_scam_signals(content), [])

    def test_trusted_domain_names_do_not_allow_suffix_or_credential_tricks(self):
        for content in (
            "https://support.discord.com.evil.example/login",
            "https://discord.com@evil.example/login",
            "https://evil-discord.com/login",
            "https://discord.com/?redirect=https://evil.example/login",
        ):
            with self.subTest(content=content):
                self.assertTrue(detect_scam_signals(content))


if __name__ == "__main__":
    unittest.main()