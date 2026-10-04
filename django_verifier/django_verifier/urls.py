from django.urls import path

from .views import status, verify

urlpatterns = [
    path("status", status),
    path("verify", verify),
]
