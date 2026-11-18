"""One-time Windows registration, per logged-in user, no stored password."""
from pathlib import Path
import datetime as dt
import sys
import hashlib

from .supervisor import interpreter, state_directory, start_background

TASK_NAME = 'SXM Metadata Recorder'


def task_name(user=None):
    if user is None:
        import win32api
        user = win32api.GetUserNameEx(2)
    return TASK_NAME + '_' + hashlib.sha256(user.casefold().encode('utf-8')).hexdigest()[:12]


def task_service():
    import win32com.client
    service = win32com.client.Dispatch('Schedule.Service')
    service.Connect()
    return service


def current_user_sid():
    """Read the signed-in identity from the token without a domain lookup."""
    import win32api
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        return win32security.ConvertSidToStringSid(sid)
    finally:
        token.Close()


def setup_error_message(exc):
    code = exc.hresult
    if exc.excepinfo and exc.excepinfo[5]:
        code = exc.excepinfo[5]
    code &= 0xffffffff
    if code == 0x8007052e:
        detail = ('Windows could not associate automatic startup with the signed-in account. '
                  'Close this window and reopen SXM Metadata under the Windows account used for measurements. '
                  'No password is requested or stored.')
    elif code == 0x80070005:
        detail = ('Windows denied permission to register automatic startup. '
                  'Ask the instrument maintainer to check Task Scheduler permissions for this Windows account.')
    else:
        detail = 'Windows Task Scheduler could not complete automatic startup setup.'
    return detail + f'\nWindows error: 0x{code:08X}.'


def install(validate_only=False):
    import pywintypes
    try:
        return _install(validate_only)
    except pywintypes.com_error as exc:
        from .recorder import atomic_json, stamp
        import traceback
        message = setup_error_message(exc)
        diagnostic = state_directory() / 'startup_error.json'
        try:
            diagnostic.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(diagnostic, dict(stamp(), message=message, exception=repr(exc),
                                        traceback=traceback.format_exc()))
            message += '\nDetails saved to: ' + str(diagnostic)
        except OSError:
            message += '\nThe diagnostic file could not be saved.'
        raise RuntimeError(message) from exc


def _install(validate_only=False):
    if sys.version_info < (3, 10):
        raise RuntimeError('Instrument maintainer: Python 3.10 or newer is required for one-time setup.')
    import win32api
    import win32file
    service = task_service()
    folder = service.GetFolder('\\')
    definition = service.NewTask(0)
    user = win32api.GetUserNameEx(2)  # NameSamCompatible: DOMAIN\user
    definition.RegistrationInfo.Description = 'Captures SXM spectroscopy metadata and creates separate enriched copies.'
    # Leave principal credentials unset: Task Scheduler inherits the account of
    # the current Connect() session. Explicit SAM name + empty password can fail
    # with ERROR_LOGON_FAILURE even though interactive logon needs no password.
    definition.Principal.LogonType = 3  # TASK_LOGON_INTERACTIVE_TOKEN
    definition.Principal.RunLevel = 0
    settings = definition.Settings
    settings.Enabled = True
    settings.StartWhenAvailable = True
    settings.DisallowStartIfOnBatteries = False
    settings.StopIfGoingOnBatteries = False
    settings.ExecutionTimeLimit = 'PT0S'
    settings.MultipleInstances = 2  # IgnoreNew
    settings.RestartInterval = 'PT1M'
    settings.RestartCount = 3
    trigger = definition.Triggers.Create(9)  # Logon
    trigger.UserId = current_user_sid()
    trigger.Enabled = True
    trigger.Repetition.Interval = 'PT1M'
    trigger.Repetition.StopAtDurationEnd = False
    recovery = definition.Triggers.Create(1)  # Time trigger also works during this sign-in.
    recovery.StartBoundary = (dt.datetime.now().astimezone() + dt.timedelta(seconds=10)).isoformat(timespec='seconds')
    recovery.Enabled = True
    recovery.Repetition.Interval = 'PT1M'
    recovery.Repetition.StopAtDurationEnd = False
    action = definition.Actions.Create(0)
    action.Path = interpreter()
    action.Arguments = '-B -m sxm_anfatec.supervisor'
    action.WorkingDirectory = str(Path(__file__).resolve().parent.parent)
    # Create/update and use the existing interactive Windows token; no password.
    registered = folder.RegisterTaskDefinition(task_name(user), definition, 1 if validate_only else 6, None, None, 3)
    if validate_only:
        return 'Task XML syntax validated only; account registration not tested. No task installed or process started.'
    remove_legacy_startup()
    start_background()
    return registered.Path


def remove_legacy_startup():
    """Remove only this recorder's earlier per-user startup entry."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r'Software\Microsoft\Windows\CurrentVersion\Run', 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, 'SXM Metadata Recorder')
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def registered_task():
    import pywintypes
    try:
        return task_service().GetFolder('\\').GetTask(task_name())
    except pywintypes.com_error as exc:
        codes = [exc.hresult]
        if exc.excepinfo:
            codes.append(exc.excepinfo[5])
        if any(code is not None and (code & 0xffffffff) in (0x80070002, 0x80070003) for code in codes):
            return None
        raise  # Access/policy failures must not be reported as disabled.


def disable():
    task = registered_task()
    if task is not None:
        task.Enabled = False
        if task.Enabled:
            raise RuntimeError('Windows did not disable automatic startup.')
    remove_legacy_startup()


def stop_completely(state=None, timeout=30):
    """Persist the stop, disable relaunches, and verify shutdown before returning."""
    from .supervisor import stop_background
    state = Path(state or state_directory())
    state.mkdir(parents=True, exist_ok=True)
    (state / 'PAUSED').touch()
    errors = []
    try:
        disable()
    except Exception as exc:
        errors.append('Automatic startup could not be disabled: ' + str(exc))
    try:
        stop_background(state, timeout=timeout)
    except Exception as exc:
        errors.append(str(exc))
    if errors:
        raise RuntimeError('\n'.join(errors) + '\nThe persistent stop request remains set. See STOP_AND_RESTART.md.')


def uninstall():
    stop_completely()
    if registered_task() is not None:
        task_service().GetFolder('\\').DeleteTask(task_name(), 0)


def installed():
    try:
        return bool(task_service().GetFolder('\\').GetTask(task_name()).Enabled)
    except Exception:
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Start, stop or remove automatic SXM metadata recording.')
    parser.add_argument('command', choices=('start', 'stop', 'remove'))
    args = parser.parse_args()
    if args.command == 'start':
        install()
        print('Automatic recording enabled and start requested.')
    elif args.command == 'stop':
        stop_completely()
        print('Stopped completely; automatic startup disabled.')
    else:
        uninstall()
        print('Stopped; automatic startup registration removed. Recorded files retained.')


if __name__ == '__main__':
    main()
