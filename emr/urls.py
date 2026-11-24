from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = f'{settings.CLINIC_NAME} — Admin'
admin.site.site_title = 'Admin EMR'
admin.site.index_title = 'Data master'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
]
