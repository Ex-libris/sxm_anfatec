"""Offline stop/restart tests. No tasks installed and no instrument access."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from sxm_anfatec import startup, supervisor
from sxm_anfatec.recorder import Recorder


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        # No interaction with an actual legacy recorder on the test machine.
        self.home = patch('pathlib.Path.home', return_value=self.root)
        self.home.start()

    def tearDown(self):
        self.home.stop()
        self.temp.cleanup()

    def test_disable_existing_and_absent_task_is_idempotent(self):
        task = Mock(Enabled=True)
        with patch.object(startup, 'registered_task', return_value=task), patch.object(startup, 'remove_legacy_startup') as legacy:
            startup.disable()
            startup.disable()
            self.assertFalse(task.Enabled)
            self.assertEqual(legacy.call_count, 2)
        with patch.object(startup, 'registered_task', return_value=None), patch.object(startup, 'remove_legacy_startup'):
            startup.disable()

    def test_absent_task_is_distinguished_from_denied_access(self):
        import pywintypes
        service = Mock()
        with patch.object(startup, 'task_service', return_value=service), patch.object(startup, 'task_name', return_value='test'):
            service.GetFolder.return_value.GetTask.side_effect = pywintypes.com_error(-2147024894, 'Not found', None, None)
            self.assertIsNone(startup.registered_task())
            service.GetFolder.return_value.GetTask.side_effect = pywintypes.com_error(-2147024891, 'Access denied', None, None)
            with self.assertRaises(pywintypes.com_error):
                startup.registered_task()

    def test_disable_failure_still_requests_shutdown_and_is_reported(self):
        with patch.object(startup, 'disable', side_effect=PermissionError('policy')), patch.object(supervisor, 'stop_background') as stop:
            with self.assertRaisesRegex(RuntimeError, 'could not be disabled'):
                startup.stop_completely(self.state)
            stop.assert_called_once()
            self.assertTrue((self.state / 'PAUSED').exists())

    def test_active_lock_prevents_false_shutdown_confirmation(self):
        import msvcrt
        folder = self.state / 'recordings'
        folder.mkdir()
        with (folder / 'recorder.lock').open('w+b') as guard:
            guard.write(b'0')
            guard.flush()
            guard.seek(0)
            msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
            with self.assertRaisesRegex(RuntimeError, 'Shutdown was not confirmed'):
                supervisor.stop_background(self.state, timeout=0)
            self.assertTrue((folder / 'STOP').exists())
        supervisor.stop_background(self.state, timeout=0)
        supervisor.stop_background(self.state, timeout=0)

    def test_stop_also_signals_configured_legacy_recorder(self):
        legacy = self.root / 'old_output'
        legacy.mkdir()
        (self.root / 'SXM_metadata_recorder.json').write_text(json.dumps({'output': str(legacy)}))
        supervisor.stop_background(self.state)
        self.assertTrue((legacy / 'STOP').exists())

    def test_paused_late_child_never_starts_instrument_workers(self):
        (self.state / 'PAUSED').touch()
        recorder = Recorder(self.state / 'recordings', follow_sxm=True)
        recorder.dde_worker = Mock()
        recorder.topo_worker = Mock()
        recorder.run()
        recorder.dde_worker.assert_not_called()
        recorder.topo_worker.assert_not_called()
        self.assertFalse(supervisor.busy_workers(self.state))

    def test_start_clears_persistent_stop_and_launches_hidden(self):
        (self.state / 'PAUSED').touch()
        with patch.object(supervisor, 'state_directory', return_value=self.state), patch.object(supervisor.subprocess, 'Popen') as launch:
            supervisor.start_background()
        self.assertFalse((self.state / 'PAUSED').exists())
        self.assertIn('sxm_anfatec.supervisor', launch.call_args.args[0])
        self.assertEqual(launch.call_args.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)

    def test_removal_requires_confirmed_stop(self):
        service = Mock()
        with patch.object(startup, 'stop_completely', side_effect=RuntimeError('still active')), patch.object(startup, 'task_service', return_value=service):
            with self.assertRaises(RuntimeError):
                startup.uninstall()
            service.GetFolder.assert_not_called()
        with patch.object(startup, 'stop_completely'), patch.object(startup, 'registered_task', return_value=Mock()), patch.object(startup, 'task_service', return_value=service), patch.object(startup, 'task_name', return_value='owned-task'):
            startup.uninstall()
            service.GetFolder.return_value.DeleteTask.assert_called_once_with('owned-task', 0)

    def test_actual_supervised_recorder_stops_and_can_restart_without_hardware(self):
        script = self.root / 'fake_instrument.py'
        package_parent = str(Path(supervisor.__file__).resolve().parent.parent)
        script.write_text('''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from sxm_anfatec.recorder import Recorder
class Bridge:
    def snapshot(self): return {'parameters': {}}
r = Recorder(Path(sys.argv[1]) / 'recordings', follow_sxm=True,
             topo=False, auto_enrich=False, bridge_factory=Bridge)
r.dde_worker = lambda: r.stop.wait()
r.run()
''')
        command = [sys.executable, '-B', str(script), str(self.state), package_parent]
        previous_pid = None
        with patch.object(supervisor, 'child_command', return_value=command), patch.object(startup, 'disable'):
            for _ in range(2):
                (self.state / 'PAUSED').unlink(missing_ok=True)
                worker = threading.Thread(target=supervisor.serve, args=(self.state,))
                worker.start()
                try:
                    deadline = time.monotonic() + 12
                    while True:
                        status = supervisor.read_json(self.state / 'recordings/status.json')
                        if status.get('running') and status.get('pid') != previous_pid:
                            previous_pid = status['pid']
                            break
                        if time.monotonic() > deadline:
                            self.fail('Recorder did not start')
                        time.sleep(.05)
                    startup.stop_completely(self.state, timeout=20)
                    self.assertFalse(supervisor.busy_workers(self.state))
                    self.assertFalse(supervisor.read_json(self.state / 'recordings/status.json')['running'])
                finally:
                    (self.state / 'PAUSED').touch()
                    worker.join(25)
                self.assertFalse(worker.is_alive())
                # A delayed scheduled invocation must not restart while stopped.
                with patch.object(supervisor.subprocess, 'Popen') as launch:
                    supervisor.serve(self.state)
                    launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
