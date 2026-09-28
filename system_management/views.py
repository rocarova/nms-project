from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib import messages
from django.http import Http404
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import BackupSettingsForm, EmailSettingsForm, SyslogSettingsForm
from .models import SystemSettings
from .network import server_lan_ip
from .notifications import describe_error, recipients, send_test

# The listener reports in every few seconds; older than this means it isn't running
LISTENER_STALE_AFTER = timedelta(seconds=30)
SETTINGS_TABS = ['account', 'syslog', 'backups', 'email']


def _safe_next(request):
    # Only follow ?next= if it points back to this site
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()},
                                                    require_https=request.is_secure()):
        return next_url
    return None


# Create your views here.
def signupuser(request):
    if not django_settings.ALLOW_SIGNUP:
        raise Http404('Sign-up is disabled')
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'GET':
        return render(request, 'system_management/signupuser.html', {'form': UserCreationForm()})

    # UserCreationForm checks for duplicate usernames, matching passwords and the password validators
    form = UserCreationForm(request.POST)
    if form.is_valid():
        user = form.save()
        login(request, user)
        return redirect('dashboard')
    return render(request, 'system_management/signupuser.html', {'form': form})


def logoutuser(request):
    if request.method == 'POST':
        logout(request)
    return redirect('login')


def loginuser(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request) or 'dashboard')

    if request.method == 'GET':
        # Sends the login page to the user
        return render(request, 'system_management/login.html',
                      {'form': AuthenticationForm(), 'next': request.GET.get('next', '')})

    # Authenticate the user
    form = AuthenticationForm(request, data=request.POST)
    if form.is_valid():
        login(request, form.get_user())
        return redirect(_safe_next(request) or 'dashboard')
    return render(request, 'system_management/login.html',
                  {'form': AuthenticationForm(), 'next': request.POST.get('next', ''),
                   'error': 'The username or the password did not match'})


@login_required
def settings_view(request):
    system = SystemSettings.load()
    forms = {
        'account': PasswordChangeForm(request.user),
        'syslog': SyslogSettingsForm(instance=system),
        'backups': BackupSettingsForm(instance=system),
        'email': EmailSettingsForm(instance=system),
    }
    tab = request.GET.get('tab') if request.GET.get('tab') in SETTINGS_TABS else 'account'

    if request.method == 'POST':
        tab = request.POST.get('section')
        if tab == 'account':
            form = PasswordChangeForm(request.user, request.POST)
        elif tab == 'syslog':
            form = SyslogSettingsForm(request.POST, instance=system)
        elif tab == 'backups':
            form = BackupSettingsForm(request.POST, instance=system)
        elif tab == 'email':
            sending_test = request.POST.get('action') == 'test'
            form = EmailSettingsForm(request.POST, instance=system, require_server=sending_test)
        else:
            return redirect('settings')

        if form.is_valid():
            form.save()
            if tab == 'account':
                update_session_auth_hash(request, form.user)  # Keep the user signed in after changing password
                messages.success(request, 'Your password has been changed.')
            elif tab == 'email' and sending_test:
                try:
                    send_test(form.instance)
                    messages.success(request, f"Settings saved. A test email was sent to {', '.join(recipients(form.instance))}.")
                except Exception as exc:
                    messages.error(request, f'Settings saved, but the test email failed: {describe_error(exc, form.instance)}')
            else:
                messages.success(request, f'{tab.capitalize()} settings saved.')
            return redirect(f"{reverse('settings')}?tab={tab}")
        forms[tab] = form

    listener_running = bool(system.listener_seen_at and timezone.now() - system.listener_seen_at < LISTENER_STALE_AFTER)
    return render(request, 'system_management/settings.html', {
        'forms': forms,
        'tab': tab,
        'system': system,
        'listener_running': listener_running,
        'has_smtp_password': bool(system.smtp_password),
        'server_ip': server_lan_ip(request),
    })
