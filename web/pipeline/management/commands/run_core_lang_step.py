import json

from django.core.management.base import BaseCommand, CommandError

from pipeline import views


class Command(BaseCommand):
    help = "Runs an isolated Block 02 core step for one edition/language."

    def add_arguments(self, parser):
        parser.add_argument("--edition-id", type=int, required=True)
        parser.add_argument("--step", required=True, choices=["translate", "refine", "polish"])
        parser.add_argument("--target-language", default="")
        parser.add_argument("--translate-agent-name", default="")
        parser.add_argument("--refine-profile", default="")

    def handle(self, *args, **options):
        try:
            result = views.execute_language_isolated_core_step(
                edition_id=options["edition_id"],
                step=options["step"],
                target_language=options["target_language"] or None,
                translate_agent_name=options["translate_agent_name"] or None,
                refine_profile=options["refine_profile"] or None,
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(json.dumps(result, ensure_ascii=True, default=str))
