"""URL configuration for email-service."""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.decorators.clickjacking import xframe_options_exempt
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.actions.scheduled_job_views import (
    ScheduledJobRunDetailView,
    ScheduledJobRunsView,
    ScheduledJobsView,
)
from apps.email.public_pages import confirm_page, unsubscribe_page

from . import views

urlpatterns = [
    path('', views.root, name='root'),
    path('api/v1/', include('apps.email.urls')),
    # Detail before ``<job>/runs`` so ``runs/<id>`` is not parsed as a job name.
    path('api/v1/scheduled-jobs', ScheduledJobsView.as_view(), name='scheduled-jobs'),
    path(
        'api/v1/scheduled-jobs/runs/<int:pk>',
        ScheduledJobRunDetailView.as_view(),
        name='scheduled-job-run',
    ),
    path(
        'api/v1/scheduled-jobs/<str:job>/runs',
        ScheduledJobRunsView.as_view(),
        name='scheduled-job-runs',
    ),
    path('api/v1/actions/', include('apps.actions.urls')),
    path('u/<str:token>', unsubscribe_page, name='email-unsubscribe'),
    path('n/confirm/<str:token>', confirm_page, name='newsletter-confirm'),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path(
        'api/docs/',
        xframe_options_exempt(SpectacularSwaggerView.as_view(url_name='schema')),
        name='swagger-ui',
    ),
    path(
        'api/docs/redoc/',
        xframe_options_exempt(SpectacularRedocView.as_view(url_name='schema')),
        name='redoc',
    ),
]

if settings.DJANGO_ADMIN_ENABLED:
    urlpatterns.insert(1, path('admin/', admin.site.urls))

handler400 = 'config.errors.bad_request'
handler403 = 'config.errors.permission_denied'
handler404 = 'config.errors.page_not_found'
handler500 = 'config.errors.server_error'
