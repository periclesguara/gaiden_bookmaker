from collections_module.models import Collection
from editorial.models import Edition
from gaiden.application.builds.finalized_projects import finalized_project_builds
from pipeline.models import IntakeItem
from web.writer.models import Manuscript


def home_projection() -> dict:
    finalized = finalized_project_builds()
    finalized_edition_ids = {build.edition_id for build in finalized}
    total_editions = Edition.objects.count()
    cards = [
        {
            "key": "intake",
            "title": "Intake",
            "description": "Import sources, manage Drive and prepare deterministic official bodies.",
            "count": IntakeItem.objects.filter(status="REGISTERED").count(),
            "status": "Registered sources",
            "route": "intake:home",
            "action": "Open Intake",
        },
        {
            "key": "collections",
            "title": "Collections",
            "description": "Assemble collections from identified, approved editorial editions.",
            "count": Collection.objects.count(),
            "status": "Collection projects",
            "route": "collection_new",
            "action": "Open Collections",
        },
        {
            "key": "manual",
            "title": "Bookmaker — Manual / AI",
            "description": "Run Translate, Refine, Polish and manual editorial production.",
            "count": max(total_editions - len(finalized_edition_ids), 0),
            "status": "Editions available for production",
            "route": "manual_ai:home",
            "action": "Open Bookmaker",
        },
        {
            "key": "writer",
            "title": "Writer",
            "description": "Write, compare, approve and explicitly promote immutable manuscript versions.",
            "count": Manuscript.objects.count(),
            "status": "Manuscripts",
            "route": "writer:home",
            "action": "Open Writer",
        },
        {
            "key": "finalized",
            "title": "Projetos Finalizados",
            "description": "Review valid final editions, validation evidence, history and downloads.",
            "count": len(finalized),
            "status": "Validated final builds",
            "route": "finalized_projects:list",
            "action": "View Finalized Projects",
        },
    ]
    return {"cards": cards, "total_modules": len(cards)}
