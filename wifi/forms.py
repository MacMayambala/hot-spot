from django import forms
from .models import WifiPackage

class CustomerPhoneForm(forms.Form):
    phone_number = forms.CharField(
        max_length=15,
        label='Mobile Number',
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'e.g., 0700000000',
            'autocomplete': 'tel',
            'inputmode': 'numeric',
        })
    )
    name = forms.CharField(
        max_length=100,
        required=False,
        label='Your Name (optional)',
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Enter your name',
            'autocomplete': 'name',
        })
    )

    def clean_phone_number(self):
        phone = self.cleaned_data['phone_number']
        if phone.startswith('0'):
            phone = '+256' + phone[1:]
        elif not phone.startswith('+256'):
            phone = '+256' + phone
        return phone

class PackageSelectForm(forms.Form):
    package = forms.ModelChoiceField(
        queryset=WifiPackage.objects.filter(is_active=True),
        empty_label=None,
        widget=forms.Select(attrs={
            'class': 'form-select form-select-lg',
        })
    )

class PackageForm(forms.ModelForm):
    class Meta:
        model = WifiPackage
        fields = ['name', 'description', 'price', 'duration', 'duration_unit',
                  'data_limit', 'speed_limit', 'is_unlimited', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Apply Bootstrap classes to all fields
        for field_name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'