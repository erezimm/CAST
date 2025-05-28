from django.shortcuts import render
from django.http import Http404
from django.conf import settings
from datetime import datetime,timedelta
import os
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
    # plot_rel_path = os.path.join("LAST", "plots", filename)
    # plot_abs_path = os.path.join(settings.STATIC_ROOT, plot_rel_path)

    # Generate plot if not already saved
    try:
        plot_fields(date_str=night)
    except Exception as e:
        raise Http404(f"Failed to generate plot: {str(e)}")

    context = {
        'plot_path': f"{settings.STATIC_ROOT}/LAST/plots/{filename}",
        'night': night,
    }
    return render(request, 'observed_fields_plot.html', context)