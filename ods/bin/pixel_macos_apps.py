"""Owner-session app launch action; caller supplies authenticated policy and lock.

Not a shell bridge. Approved app paths must come from owner configuration,
never the model request. The caller must serialize this with access changes.
"""
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess


class AppLaunchError(ValueError):
    pass


def launch_application(request, *, approved_apps, access_status):
    if (type(request) is not dict or set(request) != {'bundleId'}
            or type(request['bundleId']) is not str
            or not re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', request['bundleId'])):
        raise AppLaunchError('invalid-app-request')
    bundle_id = request['bundleId']
    if type(approved_apps) is not dict or bundle_id not in approved_apps:
        raise AppLaunchError('app-not-approved')
    if platform.system() != 'Darwin' or os.getuid() == 0:
        raise AppLaunchError('owner-macos-session-required')
    if os.stat('/dev/console').st_uid != os.getuid():
        raise AppLaunchError('owner-console-session-required')
    app = Path(approved_apps[bundle_id])
    if not app.is_absolute() or app.suffix != '.app' or app.resolve(strict=True) != app:
        raise AppLaunchError('approved-app-path-invalid')
    with (app / 'Contents/Info.plist').open('rb') as handle:
        info = plistlib.load(handle)
    if info.get('CFBundleIdentifier') != bundle_id:
        raise AppLaunchError('approved-app-identity-changed')
    status = access_status()
    if (type(status) is not dict or status.get('scope') != 'owner-host'
            or status.get('available') is not True or status.get('pending') is not False
            or status.get('configured_mode') != 'full-access'
            or status.get('effective_mode') != 'full-access'
            or status.get('runtime_verified') is not True or status.get('reason') is not None):
        raise AppLaunchError('verified-full-access-required')
    try:
        result = subprocess.run(['/usr/bin/open', '-a', str(app)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=False)
    except subprocess.TimeoutExpired:
        # Opening may already have happened; callers must not retry blindly.
        raise AppLaunchError('app-launch-outcome-unknown') from None
    if result.returncode != 0:
        raise AppLaunchError('app-launch-failed')
    return {'bundleId': bundle_id, 'status': 'launch-requested'}
