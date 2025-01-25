from django import forms
from .models import Candidate

class CandidateForm(forms.ModelForm):
    class Meta:
        model = Candidate
        fields = ['name', 'ra', 'dec']
        widgets = {
            'ra': forms.TextInput(attrs={'placeholder': 'Right Ascension'}),
            'dec': forms.TextInput(attrs={'placeholder': 'Declination'}),
        }
        labels = {
            'ra': 'RA (Right Ascension)',
            'dec': 'Dec (Declination)',
        }

class FileUploadForm(forms.Form):
    file = forms.FileField(label='Select a file')
    # image = forms.ImageField(label='Upload Image', required=False)  # Optional image upload