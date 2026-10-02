"""URL configuration for email-service."""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.decorators.clickjacking import xframe_options_exempt
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.email.views import unsubscribe_page

from . import views

urlpatterns = [
    path('', views.root, name='root'),
    path('api/v1/', include('apps.email.urls')),
    path('api/v1/actions/', include('apps.actions.urls')),
    path('u/<str:token>', unsubscribe_page, name='email-unsubscribe'),
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
