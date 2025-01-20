from .forms import FileUploadForm
from .utils import process_json_file, add_candidate_as_target, check_target_exists_for_candidate, generate_photometry_graph
from .models import Candidate
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.urls import reverse

def upload_file_view(request):
    """
    Handles file uploads and processes candidates from a JSON file.
    """
    if request.method == 'POST':
        form = FileUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = request.FILES['file']  # Get the uploaded file

            try:
                # Process the uploaded file directly (no need to save temporarily)
                candidate_count = process_json_file(uploaded_file)
                if candidate_count is None:
                    messages.warning(request, "Candidate already exists.")
                else:
                    messages.success(request, f"Successfully processed {candidate_count} candidates.")
            except Exception as e:
                # Handle any errors during file processing
                messages.error(request, f"Error processing file: {e}")

            # Redirect to the candidate list view
            return redirect('candidates:list')
    else:
        form = FileUploadForm()

    return render(request, 'candidates/upload.html', {'form': form})


def delete_candidate_view(request):
    """
    Handles deletion of a candidate via form submission.
    """
    if request.method == 'POST':
        candidate_id = request.POST.get('candidate_id')  # Get candidate ID from the form
        candidate = get_object_or_404(Candidate, id=candidate_id)
        
        # Delete the candidate
        candidate_name = candidate.name
        candidate.delete()
        
        # Add a success message
        messages.success(request, f"Candidate '{candidate_name}' has been deleted.")
    
    # Redirect back to the candidate list
    return redirect('candidates:list')

def candidate_list_view(request):
    """
    Display a list of candidates with a filter for real/bogus status.
    """
    filter_value = request.GET.get('filter', 'all')  # Get filter value from URL, default to 'all'

    # Apply filtering based on the filter_value 
    if filter_value == 'real':
        candidates = Candidate.objects.filter(real_bogus=True).order_by('-created_at')
    elif filter_value == 'bogus':
        candidates = Candidate.objects.filter(real_bogus=False).order_by('-created_at')
    elif filter_value == 'neither':
        candidates = Candidate.objects.filter(real_bogus__isnull=True).order_by('-created_at')
    else:  # 'all'
        candidates = Candidate.objects.all().order_by('-created_at')
        
    candidate_status = [
        {
            'candidate': candidate,
            'target': check_target_exists_for_candidate(candidate.id),  # Include Target if it exists
            'graph': generate_photometry_graph(candidate)  # Generate photometry graph
        }
        for candidate in candidates
    ]

    return render(request, 'candidates/list.html', {
        # 'candidates': candidates,
        'filter_value': filter_value, # Pass the current filter to the template
        'candidate_status': candidate_status 
    })

def add_target_view(request):
    """
    Adds a candidate as a TOM target.
    """
    if request.method == 'POST':
        candidate_id = request.POST.get('candidate_id')
        try:
            target = add_candidate_as_target(candidate_id)
            messages.success(request, f"Candidate added as target: {target.name}")
        except Exception as e:
            messages.error(request, f"Failed to add target: {str(e)}")
    return redirect('candidates:list')

def cone_search_view(request):
    """
    View to perform a cone search for targets by RA/Dec using `cone_search_filter`.
    """
    try:
        # Parse query parameters
        ra = float(request.GET.get('ra'))
        dec = float(request.GET.get('dec'))
        radius_arcsec = float(request.GET.get('radius', 3))  # Default radius is 3 arcseconds

        # Convert radius to degrees
        radius_deg = radius_arcsec / 3600.0

        # Perform the cone search
        queryset = Target.objects.all()
        matching_targets = cone_search_filter(queryset, ra, dec, radius_deg)

        # Prepare the results
        results = [
            {
                'id': target.id,
                'name': target.name,
                'ra': target.ra,
                'dec': target.dec,
                'extra_fields': target.extra_fields,
            }
            for target in matching_targets
        ]

        return JsonResponse({'success': True, 'results': results})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)
    
def update_real_bogus_view(request, candidate_id):
    """
    Updates the real/bogus status of a candidate based on the button clicked.
    """
    if request.method == 'POST':
        candidate = get_object_or_404(Candidate, id=candidate_id)
        real_bogus = request.POST.get('real_bogus')

        # Map the input to the appropriate value
        if real_bogus == 'real':
            candidate.real_bogus = True
        elif real_bogus == 'bogus':
            candidate.real_bogus = False
        else:
            messages.error(request, "Invalid real/bogus value selected.")
            return redirect('candidates:list')

        candidate.save()
        messages.success(request, f"Updated {candidate.name} to {candidate.get_real_bogus_display()}.")

    # Get the filter parameter from the request
    filter_value = request.GET.get('filter', 'all')  # Default to 'neither' if no filter is provided

    # Redirect to the candidate list with the current filter applied
    return redirect(f"{reverse('candidates:list')}?filter={filter_value}")

def candidate_detail_view(request, candidate_id):
    candidate = get_object_or_404(Candidate, id=candidate_id)
    photometry = candidate.photometry.all()
    return render(request, 'candidates/detail.html', {
        'candidate': candidate,
        'photometry': photometry
    })