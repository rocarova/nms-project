from django.forms import ModelForm, TextInput, Textarea, PasswordInput
from .models import Device, Vendor, Location

class DeviceForm(ModelForm):
    class Meta:
        model = Device
        fields = ['hostname', 'ip_address', 'vendor', 'username', 'password', 'location', 'enabled']
        labels = {
            'ip_address': 'IP address',
            'enabled': 'Enabled',
        }
        widgets = {
            'hostname': TextInput(attrs={'placeholder': 'e.g. core-sw1', 'autocomplete': 'off'}),
            'ip_address': TextInput(attrs={'placeholder': 'e.g. 10.0.0.1', 'autocomplete': 'off', 'class': 'mono'}),
            'username': TextInput(attrs={'placeholder': 'e.g. netops', 'autocomplete': 'off'}),
            'password': PasswordInput(attrs={'placeholder': '••••••••', 'autocomplete': 'new-password'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['vendor'].empty_label = 'Select a vendor'
        self.fields['location'].empty_label = 'Select a location'
        if self.instance.pk:
            # Editing: the saved password is never sent back to the browser, so blank means "keep it"
            self.fields['password'].required = False
            self.fields['password'].widget.attrs['placeholder'] = 'Leave blank to keep the current password'

    def clean_password(self):
        return self.cleaned_data['password'] or self.instance.password

class VendorForm(ModelForm):
    class Meta:
        model = Vendor
        fields = ['name', 'description']

        # This adds the styling classes your CSS expects
        widgets = {
            'name': TextInput(attrs={'placeholder': 'e.g. Cisco, Juniper...'}),
            'description': Textarea(attrs={'rows': 3, 'placeholder': 'Optional vendor details...'}),
        }

class LocationForm(ModelForm):
    class Meta:
        model = Location
        fields = ['name']

        # This adds the styling classes your CSS expects
        widgets = {
            'name': TextInput(attrs={'placeholder': 'e.g. Houston, Guadalajara...'}),
        }
