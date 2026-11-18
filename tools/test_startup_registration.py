"""Regression checks for interactive account registration; no real tasks created."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import pywintypes
from sxm_anfatec import startup


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.definition = self.service.NewTask.return_value
        self.folder = self.service.GetFolder.return_value
        self.registered = self.folder.RegisterTaskDefinition.return_value
        self.registered.Path = '\\test-task'

    def test_registration_inherits_current_connection_without_credentials(self):
        with patch.object(startup, 'task_service', return_value=self.service), patch.object(startup, 'current_user_sid', return_value='S-1-5-21-test'), patch.object(startup, 'remove_legacy_startup') as retire, patch.object(startup, 'start_background') as launch:
            self.assertEqual(startup.install(), '\\test-task')
            args = self.folder.RegisterTaskDefinition.call_args.args
            self.assertEqual(args[2:], (6, None, None, 3))
            self.assertNotIn('UserId', vars(self.definition.Principal))
            self.assertEqual(self.definition.Principal.LogonType, 3)
            self.assertEqual(self.definition.Principal.RunLevel, 0)
            self.assertEqual(self.definition.Triggers.Create.return_value.UserId, 'S-1-5-21-test')
            retire.assert_called_once()
            launch.assert_called_once()

    def test_validation_does_not_claim_registration_or_start_watcher(self):
        with patch.object(startup, 'task_service', return_value=self.service), patch.object(startup, 'current_user_sid', return_value='sid'), patch.object(startup, 'remove_legacy_startup') as retire, patch.object(startup, 'start_background') as launch:
            message = startup.install(validate_only=True)
            self.assertIn('account registration not tested', message)
            self.assertEqual(self.folder.RegisterTaskDefinition.call_args.args[2:], (1, None, None, 3))
            retire.assert_not_called()
            launch.assert_not_called()

    def test_original_nested_error_is_readable_logged_and_does_not_launch(self):
        original = pywintypes.com_error(-2147352567, 'Exception occurred.', (0, None, None, None, 0, -2147023570), None)
        self.folder.RegisterTaskDefinition.side_effect = original
        with tempfile.TemporaryDirectory() as directory, patch.object(startup, 'state_directory', return_value=Path(directory)), patch.object(startup, 'task_service', return_value=self.service), patch.object(startup, 'current_user_sid', return_value='sid'), patch.object(startup, 'start_background') as launch:
            with self.assertRaisesRegex(RuntimeError, '0x8007052E') as error:
                startup.install()
            self.assertIn('No password is requested or stored', str(error.exception))
            saved = json.loads((Path(directory) / 'startup_error.json').read_text())
            self.assertIn('RegisterTaskDefinition', saved['traceback'])
            launch.assert_not_called()

    def test_access_denied_has_distinct_guidance(self):
        error = pywintypes.com_error(-2147024891, 'Access denied', None, None)
        message = startup.setup_error_message(error)
        self.assertIn('0x80070005', message)
        self.assertIn('permissions for this Windows account', message)


if __name__ == '__main__':
    unittest.main()
