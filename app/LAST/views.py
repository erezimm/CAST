from django.shortcuts import render
from django.http import Http404
from django.conf import settings
from datetime import datetime,timedelta
from .utils import plot_fields,get_sunset_sunrise

def observed_fields_plot_view(request, night=None):
    # Allow ?night=YYYY-MM-DD to override the path variable
    night = request.GET.get('night') or night
    if night is None:
        night = datetime.utcnow()
        night_str = datetime.utcnow().strftime('%Y-%m-%d')
        sunset, sunrise = get_sunset_sunrise(night_str)
        if night < sunset:
            night -= timedelta(days=1)
            night = night.strftime('%Y-%m-%d')
        else:
            night = night.strftime('%Y-%m-%d')
        

    # Prepare file paths
    filename = f"{night}.png"

    # Generate plot if not already saved
    try:
        plot_fields(date_str=night)
    except Exception as e:
        raise Http404(f"Failed to generate plot: {str(e)}")

    night_date = datetime.strptime(night, '%Y-%m-%d').date()
    context = {
        'plot_url': f"{settings.MEDIA_URL}LAST/plots/{filename}",
        'night': night,
        'prev_night': (night_date - timedelta(days=1)).isoformat(),
        'next_night': (night_date + timedelta(days=1)).isoformat(),
    }
    return render(request, 'observed_fields_plot.html', context)
