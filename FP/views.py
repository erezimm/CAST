import plotly.graph_objs as go
import plotly.offline as opy
from django.shortcuts import render

from .utils import get_last_fp

def force_photometry_view(request):
    context = {}

    if request.method == 'POST':
        try:
            ra = float(request.POST.get('ra'))
            dec = float(request.POST.get('dec'))
            days = int(request.POST.get('days'))

            # Dummy data and plot: y = ra * x + dec
            x_vals, y_vals = get_last_fp(ra, dec, days)

            trace = go.Scatter(x=x_vals, y=y_vals, mode='markers', name='Last FP')
            layout = go.Layout(title=f'LAST photometry from ra={ra}, dec={dec}',
                               xaxis=dict(title='Days ago', autorange='reversed'),
                               yaxis=dict(title='Apparent Magnitude'))

            fig = go.Figure(data=[trace], layout=layout)
            plot_div = opy.plot(fig, auto_open=False, output_type='div')
            context['plot_div'] = plot_div
        except Exception as e:
            context['error'] = f"Error: {e}"

    return render(request, 'forced_photometry.html', context)