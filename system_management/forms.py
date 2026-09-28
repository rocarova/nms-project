import re

from django import forms
from django.core.validators import validate_email

from . import tls
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
            'backup_retention': 'Backups to keep per device',
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
            'notify_backup_failed': 'A network device backup fails',
            'notify_config_changed': "A network device's configuration changes",
        }
        widgets = {
            'smtp_host': forms.TextInput(attrs={'placeholder': 'e.g. smtp.office365.com'}),
            'smtp_username': forms.TextInput(attrs={'autocomplete': 'off'}),
            'smtp_password': forms.PasswordInput(attrs={'autocomplete': 'new-password', 'placeholder': 'Leave blank to keep the current password'}),
            'email_from': forms.EmailInput(attrs={'placeholder': 'nms@example.com'}),
            'email_recipients': forms.Textarea(attrs={'rows': 3, 'placeholder': 'noc@example.com\noncall@example.com'}),
        }

    def __init__(self, *args, require_server=False, **kwargs):
        # Sending a test email needs the server details even while notifications are switched off
        self.require_server = require_server
        super().__init__(*args, **kwargs)

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
        if cleaned.get('email_enabled') or self.require_server:
            for field in ('smtp_host', 'email_from', 'email_recipients'):
                if not cleaned.get(field):
                    self.add_error(field, 'Required to send email.')
        return cleaned


MAX_UPLOAD_BYTES = 1024 * 1024  # Certificates and keys are a few KB


class CertificateRequestForm(forms.Form):
    """Details for a new CSR (or a self-signed certificate)."""
    common_name = forms.CharField(label='Common name (hostname)', max_length=64,
                                  widget=forms.TextInput(attrs={'placeholder': 'e.g. nms.example.com', 'class': 'mono'}))
    sans = forms.CharField(label='Additional names (SANs)', required=False,
                           widget=forms.Textarea(attrs={'rows': 2, 'class': 'mono', 'placeholder': 'nms, 10.0.99.20'}),
                           help_text='Other hostnames or IP addresses users will browse to, separated by commas or new lines. '
                                     'The common name is always included.')
    organization = forms.CharField(label='Organization', max_length=64, required=False)
    organizational_unit = forms.CharField(label='Department', max_length=64, required=False)
    locality = forms.CharField(label='City', max_length=128, required=False)
    state = forms.CharField(label='State / province', max_length=128, required=False)
    country = forms.CharField(label='Country code', max_length=2, required=False,
                              widget=forms.TextInput(attrs={'placeholder': 'e.g. US, MX'}))
    email = forms.EmailField(label='Contact email', required=False)
    key_type = forms.ChoiceField(label='Key type', choices=[], initial='rsa2048')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['key_type'].choices = tls.KEY_TYPES

    def clean_common_name(self):
        value = self.cleaned_data['common_name'].strip()
        try:
            if len(tls.parse_sans(value)) != 1:
                raise tls.CertificateError('Enter a single hostname or IP address.')
        except tls.CertificateError as exc:
            raise forms.ValidationError(str(exc))
        return value

    def clean_sans(self):
        value = self.cleaned_data['sans']
        try:
            tls.parse_sans(value)
        except tls.CertificateError as exc:
            raise forms.ValidationError(str(exc))
        return value

    def clean_country(self):
        value = self.cleaned_data['country'].strip().upper()
        if value and not re.fullmatch(r'[A-Z]{2}', value):
            raise forms.ValidationError('Use the 2-letter ISO country code, e.g. US or MX.')
        return value

    def request_kwargs(self):
        data = self.cleaned_data
        return {name: data[name] for name in ('common_name', 'organization', 'organizational_unit', 'locality',
                                              'state', 'country', 'email', 'key_type')} | {'sans_text': data['sans']}


class InstallCertificateForm(forms.Form):
    """The signed certificate from the CA (file or pasted), plus a private key only when it wasn't requested here."""
    certificate_file = forms.FileField(label='Certificate file', required=False,
                                       help_text='.pem, .crt, .cer or .p7b, ideally including the intermediate chain.')
    certificate_text = forms.CharField(label='…or paste the certificate', required=False,
                                       widget=forms.Textarea(attrs={'rows': 4, 'class': 'mono',
                                                                    'placeholder': '-----BEGIN CERTIFICATE-----'}))
    key_file = forms.FileField(label='Private key file (only if the request wasn\'t created here)', required=False)
    key_text = forms.CharField(label='…or paste the private key', required=False,
                               widget=forms.Textarea(attrs={'rows': 3, 'class': 'mono',
                                                            'placeholder': '-----BEGIN PRIVATE KEY-----'}))

    @staticmethod
    def read(upload, text):
        if upload:
            if upload.size > MAX_UPLOAD_BYTES:
                raise forms.ValidationError('That file is too large to be a certificate or key.')
            return upload.read()
        return text.strip().encode() if text and text.strip() else None

    def clean(self):
        cleaned = super().clean()
        cleaned['certificate'] = self.read(cleaned.get('certificate_file'), cleaned.get('certificate_text'))
        cleaned['key'] = self.read(cleaned.get('key_file'), cleaned.get('key_text'))
        if not cleaned['certificate']:
            raise forms.ValidationError('Choose the certificate file from your CA, or paste it.')
        return cleaned
