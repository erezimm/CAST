from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render

from cast.external_api_status import (
    get_external_api_status_snapshot,
    refresh_external_api_status,
)


@login_required
@user_passes_test(lambda user: user.is_superuser)
def external_api_status_view(request):
    if request.method == "POST":
        snapshot = refresh_external_api_status()
    else:
        snapshot = get_external_api_status_snapshot()
        if snapshot is None:
            snapshot = refresh_external_api_status()

    return render(
        request,
        "external_api_status.html",
        {
            "status_snapshot": snapshot,
            "services": snapshot.get("services", []) if snapshot else [],
        },
    )
