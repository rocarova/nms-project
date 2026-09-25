from pathlib import Path

from django.conf import settings
from django.utils.functional import SimpleLazyObject

STATIC_DIR = Path(__file__).resolve().parent / 'static'
VERSIONED_FILES = [STATIC_DIR / 'css' / 'style.css', STATIC_DIR / 'js' / 'nms.js']


def static_version(request):
    """Adds STATIC_VERSION for cache-busting (?v=...), so browsers pick up CSS/JS changes right away."""
    try:
        version = int(max(path.stat().st_mtime for path in VERSIONED_FILES))
    except OSError:
        version = 0
    return {'STATIC_VERSION': version}


def features(request):
    return {'ALLOW_SIGNUP': settings.ALLOW_SIGNUP}


def system_settings(request):
    """Adds `system_settings` (loaded only if a template uses it), e.g. {{ system_settings.syslog_port }}."""
    from .models import SystemSettings
    return {'system_settings': SimpleLazyObject(SystemSettings.load)}
