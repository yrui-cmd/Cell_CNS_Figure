import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import configured_client
import xiaomiao_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dependencies' / 'xiaomiao-api-setup' / 'scripts'))
import xiaomiao_setup


class ConfiguredClientTests(unittest.TestCase):
    def test_shared_key_only_even_with_explicit_and_environment(self):
        original = xiaomiao_client.CredentialManager.discover
        try:
            with patch.object(sys, 'argv', ['configured_client.py', 'balance']), patch.dict(os.environ, {'XIAOMIAO_API_KEY': 'OLD'}), patch.object(xiaomiao_setup, 'read_key', return_value='NEW') as read:
                def run():
                    values = xiaomiao_client.CredentialManager.discover(None, 'OTHER')
                    self.assertEqual(values, [('shared_config', 'NEW')])
                    return 0
                with patch.object(xiaomiao_client, 'main', side_effect=run):
                    self.assertEqual(configured_client.main(), 0)
                read.assert_called_once()
                self.assertEqual(os.environ['XIAOMIAO_API_KEY'], 'OLD')
        finally:
            xiaomiao_client.CredentialManager.discover = original


if __name__ == '__main__':
    unittest.main()
