from django.shortcuts import render
from django.http import Http404
from django.conf import settings
from datetime import datetime
import os
from .utils import plot_fields

def observed_fields_plot_view(request, night=None):
    # Allow ?night=YYYY-MM-DD to override the path variable
    night = request.GET.get('night') or night
    if night is None:
        night = datetime.utcnow().strftime('%Y-%m-%d')

    # Prepare file paths
    filename = f"{night}.png"
    plot_rel_path = os.path.join("static", "LAST", "plots", filename)
    plot_abs_path = os.path.join(settings.BASE_DIR, plot_rel_path)

    # Generate plot if not already saved
    try:
        plot_fields(None, None, date_str=night)
    except Exception as e:
        raise Http404(f"Failed to generate plot: {str(e)}")

    if not os.path.exists(plot_abs_path):
        raise Http404("Plot image not found.")

    context = {
        'plot_path': f"/static/LAST/plots/{filename}",
        'night': night,
    }
    return render(request, 'observed_fields_plot.html', context)