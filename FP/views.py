import plotly.graph_objs as go
import plotly.offline as opy
from django.shortcuts import render

from astropy.time import Time

from .utils import get_last_fp

def force_photometry_view(request):
    context = {}

    if request.method == 'POST':

        try:
            ra = float(request.POST.get('ra'))
            dec = float(request.POST.get('dec'))
            days = request.POST.get('days')
            max_results = request.POST.get('max_results')
            use_existing_ref = 'use_existing_ref' in request.POST
            start_date_str = request.POST.get('start_date')
            end_date_str = request.POST.get('end_date')

            if start_date_str and end_date_str:
                jd_start = Time(start_date_str, format='iso').jd
                jd_end = Time(end_date_str, format='iso').jd
            else:
                if not days:
                    context['error'] = 'You have to either specify a number of days or a start and end date.'
                    return render(request, 'forced_photometry.html', context)
                jd_end = Time.now().jd
                jd_start = jd_end - int(days)

            try:
                detections, nondetections = get_last_fp(ra, dec, jd_start, jd_end, max_results, use_existing_ref)
            except Exception as e:
                context['error'] = e
                return render(request, 'forced_photometry.html', context)

            if detections is None or nondetections is None:
                context['error'] = "No data returned for the given RA, DEC, and days."
                return render(request, 'forced_photometry.html', context)

            # non detection - use limmag for lim
            fig = go.Figure()
            jd_now = Time.now().jd
            det_trace = go.Scatter(x=jd_now - detections['jd'], y=detections['mag_psf'],
                       mode='markers', name='Detections',
                       error_y=dict(
                        type='data',
                        array= 1.0857 / detections['sn'],
                        visible=True
                        ))
            nondet_trace = go.Scatter(
            x=jd_now - nondetections['jd'], y=nondetections['limmag'],
            mode='markers', name="Non-detections",
            marker=dict(symbol='triangle-down', size=8),
            yaxis="y"
            )
            layout = go.Layout(title=f'LAST photometry from ra={ra}, dec={dec}',
                       xaxis=dict(title='Days ago', autorange='reversed'),
                       yaxis=dict(title='Apparent Magnitude', autorange="reversed"))

            fig = go.Figure(data=[det_trace, nondet_trace], layout=layout)
            plot_div = opy.plot(fig, auto_open=False, output_type='div')
            context['plot_div'] = plot_div
        except Exception as e:
            context['error'] = f"Error: {e}"

    return render(request, 'forced_photometry.html', context)