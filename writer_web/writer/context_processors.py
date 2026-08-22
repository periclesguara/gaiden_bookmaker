from django.conf import settings

def module_links(request):
    return {"gaiden_bookmaker_url": settings.GAIDEN_BOOKMAKER_URL}
