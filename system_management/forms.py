import re

from django import forms
from django.core.validators import validate_email

from .models import SystemSettings


class SettingsSectionForm(forms.ModelForm):
    """Saves only its own fields, so it never overwrites other sections or the listener's status columns."""

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save(update_fields=list(self._meta.fields))
        return instance


class SyslogSettingsForm(SettingsSectionForm):
    class Meta:
        model = SystemSettings
        fields = ['syslog_port', 'syslog_retention_days']
        labels = {'syslog_port': 'Listening port', 'syslog_retention_days': 'Keep messages for (days)'}


class BackupSettingsForm(SettingsSectionForm):
    class Meta:
        model = SystemSettings
        fields = ['backup_frequency', 'backup_time', 'backup_weekday', 'backup_retention']
        labels = {
            'backup_frequency': 'Backup frequency',
            'backup_time': 'Time of day',
            'backup_weekday': 'Day of week',
            'backup_retention': 'Backups to keep per switch',
        }
        widgets = {'backup_time': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M')}


class EmailSettingsForm(SettingsSectionForm):
    class Meta:
        model = SystemSettings
        fields = ['email_enabled', 'smtp_host', 'smtp_port', 'smtp_security', 'smtp_username', 'smtp_password',
                  'email_from', 'email_recipients', 'notify_backup_failed', 'notify_config_changed']
        labels = {
            'email_enabled': 'Enable email notifications',
            'smtp_host': 'SMTP server',
            'smtp_port': 'Port',
            'smtp_security': 'Security',
            'smtp_username': 'Username',
            'smtp_password': 'Password',
            'email_from': 'From address',
            'email_recipients': 'Recipients',
            'notify_backup_failed': 'A switch backup fails',
            'notify_config_changed': 'A switch configuration changes',
        }
        widgets = {
            'smtp_host': forms.TextInput(attrs={'placeholder': 'e.g. smtp.office365.com'}),
            'smtp_username': forms.TextInput(attrs={'autocomplete': 'off'}),
            'smtp_password': forms.PasswordInput(attrs={'autocomplete': 'new-password', 'placeholder': 'Leave blank to keep the current password'}),
            'email_from': forms.EmailInput(attrs={'placeholder': 'nms@example.com'}),
            'email_recipients': forms.Textarea(attrs={'rows': 3, 'placeholder': 'noc@example.com\noncall@example.com'}),
        }

    def clean_smtp_password(self):
        # PasswordInput never re-displays the saved value, so a blank submission means "keep it"
        return self.cleaned_data['smtp_password'] or self.instance.smtp_password

    def clean_email_recipients(self):
        addresses = [a for a in re.split(r'[\s,;]+', self.cleaned_data['email_recipients']) if a]
        for address in addresses:
            try:
                validate_email(address)
            except forms.ValidationError:
                raise forms.ValidationError(f'"{address}" is not a valid email address.')
        return '\n'.join(addresses)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('email_enabled'):
            for field in ('smtp_host', 'email_from', 'email_recipients'):
                if not cleaned.get(field):
                    self.add_error(field, 'Required when email notifications are enabled.')
        return cleaned
