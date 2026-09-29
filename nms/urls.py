"""
URL configuration for nms project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from net_tools import views as nt_views
from inventory import views as inventory_views
from system_management import views as sm_views
from dashboard import views as dashboard_views
from syslog_server import views as syslog_views
from backups import views as backup_views
from automation import views as automation_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('ping/', nt_views.ping, name='ping'),
    path('traceroute/', nt_views.traceroute, name='traceroute'),
    path('nslookup/', nt_views.nslookup, name='nslookup'),
    path('inventory', inventory_views.inventory_dashboard, name='inventory'),

    # Auth
    path('', sm_views.loginuser, name='login'),
    path('signup/', sm_views.signupuser, name='signup'),
    path('logout/', sm_views.logoutuser, name='logoutuser'),
    path('settings/', sm_views.settings_view, name='settings'),
    path('settings/certificate/request.csr', sm_views.download_csr, name='download_csr'),
    path('settings/certificate/certificate.pem', sm_views.download_certificate, name='download_certificate'),

    # Inventory
    path('dashboard/', dashboard_views.dashboard, name='dashboard'),
    path('add_device/', inventory_views.add_device, name='add_device'),
    path('add_vendor/', inventory_views.add_vendor, name='add_vendor'),
    path('add_location/', inventory_views.add_location, name='add_location'),
    path('inventory/<int:device_id>/edit/', inventory_views.edit_device, name='edit_device'),
    path('inventory/<int:device_id>/delete/', inventory_views.delete_device, name='delete_device'),
    path('inventory/<int:device_id>/ssh/', inventory_views.ssh_console, name='ssh_console'),

    # Backups
    path('inventory/<int:device_id>/backups/', backup_views.device_backups, name='device_backups'),
    path('inventory/<int:device_id>/backups/run/', backup_views.request_backup, name='request_backup'),
    path('inventory/<int:device_id>/backups/compare/', backup_views.backup_compare, name='backup_compare'),
    path('inventory/<int:device_id>/backups/<int:backup_id>/', backup_views.backup_detail, name='backup_detail'),
    path('inventory/<int:device_id>/backups/<int:backup_id>/download/', backup_views.backup_download, name='backup_download'),

    # Automation (push commands to devices)
    path('automation/', automation_views.history, name='automation'),
    path('automation/push/', automation_views.push, name='push'),
    path('automation/push/<int:job_id>/', automation_views.job_detail, name='push_job'),
    path('automation/push/<int:job_id>/status/', automation_views.job_status, name='push_job_status'),
    path('automation/scripts/new/', automation_views.script_edit, name='script_new'),
    path('automation/scripts/<int:script_id>/', automation_views.script_edit, name='script_edit'),
    path('automation/scripts/<int:script_id>/delete/', automation_views.script_delete, name='script_delete'),

    # network Tools
    path('network_tools/', nt_views.tools, name='tools'),

    # Syslog
    path('syslog/', syslog_views.syslog, name='syslog'),
    path('syslog/api/messages/', syslog_views.messages_api, name='syslog_messages'),
]
