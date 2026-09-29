from django.contrib import admin

from .models import PushJob, PushResult, Script


@admin.register(Script)
class ScriptAdmin(admin.ModelAdmin):
    list_display = ('name', 'mode', 'updated_at', 'created_by')


class PushResultInline(admin.TabularInline):
    model = PushResult
    extra = 0
    fields = ('hostname', 'status', 'error', 'config_changed', 'saved')
    readonly_fields = fields


@admin.register(PushJob)
class PushJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_at', 'created_by', 'mode', 'status')
    list_filter = ('mode', 'status')
    inlines = [PushResultInline]
