# Third-party imports
import pandas as pd
import plotly.graph_objs as go
import plotly.offline as opy
from astropy.time import Time

# Django imports
from django.shortcuts import render
from django.http import HttpResponse

# Local imports
from .utils import get_last_fp

def force_photometry_view(request):
    context = {}

    if request.method == 'POST':

        try:
            ra = float(request.POST.get('ra'))
            dec = float(request.POST.get('dec'))
            fieldid = request.POST.get('fieldid')
            cropid = request.POST.get('cropid')
            mountnum = request.POST.get('mountnum')
            camnum = request.POST.get('camnum')
            days = request.POST.get('days')
            max_results = request.POST.get('max_results')
            use_existing_ref = 'use_existing_ref' in request.POST
            resub = 'resub' in request.POST
            start_date_str = request.POST.get('start_date')
            end_date_str = request.POST.get('end_date')
            timeout = int(request.POST.get('timeout', 30))

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
                fieldid = fieldid if fieldid else "''"
                cropid = int(cropid) if cropid else 0
                mountnum = int(mountnum) if mountnum else 0
                camnum = int(camnum) if camnum else 0
                detections, nondetections, fp_results = get_last_fp(ra, dec, jd_start, jd_end, 
                                                                    fieldid, cropid, mountnum, camnum,
                                                                    max_results, use_existing_ref, resub,
                                                                    timeout)
            except Exception as e:
                context['error'] = e
                return render(request, 'forced_photometry.html', context)

            if fp_results is None:
                context['error'] = "No data returned for the given RA, DEC, and days."
                return render(request, 'forced_photometry.html', context)
            
            # Save the results in the context for rendering
            request.session['fp_results'] = fp_results.to_dict(orient='records')  # store as JSON-serializable
            context['csv_available'] = True

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

def download_fp_csv(request):
    data = request.session.get('fp_results')
    if not data:
        return HttpResponse("No results to download.", status=400)

    df = pd.DataFrame(data)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="forced_photometry.csv"'
    df.to_csv(path_or_buf=response, index=False)
    return response