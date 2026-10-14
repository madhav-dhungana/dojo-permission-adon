from importlib import import_module

from django.conf import settings
from django.urls import include, path

parent_urlconf = import_module(settings.DOJO_PERMISSION_PARENT_URLCONF)

urlpatterns = [
    *parent_urlconf.urlpatterns,
    path("dojo-permission/", include("dojo_permission.urls")),
]
