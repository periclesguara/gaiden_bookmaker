from django.contrib import admin
from .models import CoverBuild, InteriorBuild, PreflightReport, PrintEdition, PrintExport, TypographyProfile

admin.site.register(TypographyProfile)
admin.site.register(PrintEdition)
admin.site.register(InteriorBuild)
admin.site.register(CoverBuild)
admin.site.register(PreflightReport)
admin.site.register(PrintExport)
