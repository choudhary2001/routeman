from django import forms


class FeedbackForm(forms.Form):
    name = forms.CharField(max_length=50)
    email = forms.EmailField()
    rating = forms.IntegerField(min_value=1, max_value=5)
    message = forms.CharField(widget=forms.Textarea)
    subscribe = forms.BooleanField(required=False)
