from django import forms

from inventory.models import Device
from . import engine
from .models import MODE_CHOICES, MODE_CONFIG, Script

COMMANDS_HELP = ('One command per line. Variables: ' + ', '.join(f'{{{{ {v} }}}}' for v in engine.VARIABLES)
                 + '. Lines starting with # are comments and are not sent.')


class ScriptForm(forms.ModelForm):
    class Meta:
        model = Script
        fields = ['name', 'description', 'mode', 'commands']
        labels = {'mode': 'Runs in'}
        help_texts = {'commands': COMMANDS_HELP}
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Standard NTP servers'}),
            'description': forms.TextInput(attrs={'placeholder': 'What it does and when to use it'}),
            'mode': forms.RadioSelect,
            'commands': forms.Textarea(attrs={'rows': 12, 'class': 'mono', 'spellcheck': 'false',
                                              'placeholder': 'ntp server 10.0.99.5\nntp server 10.0.99.6\n'
                                                             'snmp-server location {{ location }}'}),
        }

    def clean_commands(self):
        commands = self.cleaned_data['commands']
        check_commands(commands)
        return commands


def check_commands(commands):
    """Validates variables once up front (with an arbitrary device-like object), so typos show before previewing."""
    if not engine.command_lines(commands):
        raise forms.ValidationError('Enter at least one command.')
    for name in engine.VARIABLE_RE.findall(commands):
        if name not in engine.VARIABLES:
            raise forms.ValidationError(f'Unknown variable {{{{ {name} }}}}. Available: '
                                        + ', '.join(f'{{{{ {v} }}}}' for v in engine.VARIABLES))


class PushForm(forms.Form):
    devices = forms.ModelMultipleChoiceField(
        queryset=Device.objects.filter(enabled=True), widget=forms.CheckboxSelectMultiple,
        error_messages={'required': 'Select at least one device.',
                        'invalid_choice': 'One of the selected devices is disabled or no longer exists.'})
    mode = forms.ChoiceField(choices=MODE_CHOICES, initial=MODE_CONFIG, widget=forms.RadioSelect)
    script = forms.ModelChoiceField(queryset=Script.objects.all(), required=False, empty_label='— Type commands below —')
    commands = forms.CharField(help_text=COMMANDS_HELP, widget=forms.Textarea(
        attrs={'rows': 10, 'class': 'mono', 'spellcheck': 'false', 'placeholder': 'interface Vlan99\n description Management'}))
    save_config = forms.BooleanField(required=False, label='Save to startup config after pushing')
    stop_on_error = forms.BooleanField(required=False, label='Stop on first error')
    confirm = forms.CharField(required=False)

    def clean_commands(self):
        commands = self.cleaned_data['commands']
        check_commands(commands)
        return commands

    def require_confirmation(self):
        """Configuration pushes change devices: make the user type PUSH. Returns True if confirmed."""
        if self.cleaned_data['mode'] != MODE_CONFIG or self.cleaned_data.get('confirm', '').strip().upper() == 'PUSH':
            return True
        self.add_error('confirm', 'Type PUSH to confirm.')
        return False
