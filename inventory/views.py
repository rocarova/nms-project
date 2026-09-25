from django.shortcuts import get_object_or_404, render, redirect
from .forms import DeviceForm, VendorForm, LocationForm
from .models import Device
from backups.models import with_latest_backup
from system_management.network import server_lan_ip
from django.contrib.auth.decorators import login_required


# Create your views here.
@login_required
def inventory_dashboard(request):
    #Extracting all devices created from all users from the same organization
    devices = with_latest_backup(Device.objects.select_related('vendor', 'location')).order_by('hostname')
    return render(request, 'inventory/inventory.html', {'devices':devices})

@login_required
def add_device(request):
    if request.method == 'POST':
        form = DeviceForm(request.POST)
        if form.is_valid():
            new_device = form.save(commit=False)
            new_device.creator = request.user
            new_device.save()
            return redirect('inventory')
    else:
        form = DeviceForm()

    return render(request, 'inventory/add_device.html', {'form': form, 'server_ip': server_lan_ip(request)})

@login_required
def ssh_console(request, device_id):
    # The terminal itself connects over the WebSocket handled by inventory.consumers.SSHConsumer
    device = get_object_or_404(Device, pk=device_id)
    return render(request, 'inventory/ssh_console.html', {'device': device})

@login_required
def add_vendor(request):
    if request.method == 'POST':
        form = VendorForm(request.POST)
        if form.is_valid():
            form.save()
            # Back to the device form so the new vendor can be selected
            return redirect('add_device')
    else:
        form = VendorForm()

    return render(request, 'inventory/add_vendor.html', {
        'form': form
    })

@login_required
def add_location(request):
    if request.method == 'POST':
        form = LocationForm(request.POST)
        if form.is_valid():
            form.save()
            # Back to the device form so the new location can be selected
            return redirect('add_device')
    else:
        form = LocationForm()

    return render(request, 'inventory/add_location.html', {
        'form': form
    })
