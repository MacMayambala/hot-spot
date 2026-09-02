# wifi/middleware.py
from django.utils.timezone import now
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse
from datetime import datetime, timedelta

class AdminSessionTimeoutMiddleware:
    """
    Middleware to log out admin (staff) users after 30 minutes of inactivity.
    Stores last activity as an ISO string (JSON‑serializable).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if user is authenticated and is staff
        if request.user.is_authenticated and request.user.is_staff:
            # Get the last activity timestamp from session (stored as ISO string)
            last_activity_str = request.session.get('admin_last_activity')
            if last_activity_str:
                try:
                    last_activity = datetime.fromisoformat(last_activity_str)
                    # Make it timezone-aware (Django expects aware datetimes)
                    from django.utils.timezone import is_aware, make_aware
                    if not is_aware(last_activity):
                        last_activity = make_aware(last_activity)
                    # Calculate inactivity time in seconds
                    inactive_seconds = (now() - last_activity).total_seconds()
                    if inactive_seconds > 1800:  # 30 minutes
                        logout(request)
                        request.session.flush()
                        return redirect(reverse('admin:login'))
                except (ValueError, TypeError):
                    # Invalid stored value – treat as no activity
                    pass
            # Update last activity timestamp (store as ISO string)
            request.session['admin_last_activity'] = now().isoformat()
        else:
            # For non-admin users, clear the session key if present
            if 'admin_last_activity' in request.session:
                del request.session['admin_last_activity']

        response = self.get_response(request)
        return response