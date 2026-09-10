"""Offline checks: billing amounts must come from the server, never a default."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

from xiaomiao_client import XiaomiaoClient


class BillingDisplayTests(unittest.TestCase):
    def test_submit_and_status_preserve_unknown_zero_and_actual_reservation(self):
        for amount in (None, 0, 20):
            with self.subTest(amount=amount), tempfile.TemporaryDirectory() as directory:
                client = XiaomiaoClient(root=Path(directory))
                payload = {
                    "job_id": "offline_billing", "status": "processing",
                    "credits_left": 36, "billing": "reserved", "charged_at": None,
                }
                if amount is not None:
                    payload["reserved_credits"] = amount
                response = requests.Response()
                response.status_code = 200
                response._content = json.dumps(payload).encode()
                try:
                    with patch.object(client, "authenticate", return_value=(
                        "offline-test", {"available_credits": 56, "journal_available": True},
                    )), patch.object(client, "_request", return_value=response):
                        submitted = client.submit("offline drawing", [])
                        queried = client.status(submitted["job_id"])
                    for result in (submitted, queried):
                        self.assertEqual(result["reserved_credits"], amount)
                        self.assertEqual(str(result["credits_left"]), "36")
                        self.assertEqual(result["billing"], "reserved")
                        self.assertIsNone(result["charged_at"])
                finally:
                    client.session.close()


if __name__ == "__main__":
    unittest.main()
