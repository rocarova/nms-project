from django.db import models
from django.contrib.auth.models import User

# models.py
class Vendor(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    # Add other fields as needed (e.g., support_contact, website)

    def __str__(self):
        return self.name

class Location(models.Model):
    name = models.CharField(max_length=100, unique=True)
    def __str__(self): return self.name
    
# Create your models here.
class Device(models.Model):
    hostname = models.CharField(max_length=20)
    ip_address = models.GenericIPAddressField(default='')
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE)
    username = models.CharField(max_length=10)
    password = models.CharField(max_length=20)
    location = models.ForeignKey(Location, on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True)
    enabled = models.BooleanField(default=True)
    creator = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.hostname
    
