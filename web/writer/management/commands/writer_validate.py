"""Audit stored chapter sessions without writing the manuscript or calling a model."""
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from web.writer.models import Chapter
from web.writer.services.validation import scene_contract
from gaiden.writer_engine.validation import validate_scene
from gaiden.writer_engine.repair import write_new


class Command(BaseCommand):
    help = 'Read-only mechanical audit of existing scenes; no model calls or manuscript changes.'

    def add_arguments(self, parser):
        parser.add_argument('chapter_id', type=int)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        try:
            chapter = Chapter.objects.select_related('project').get(pk=options['chapter_id'])
        except Chapter.DoesNotExist as exc:
            raise CommandError('Chapter not found') from exc
        previous = ''
        reports = []
        for session in chapter.sessions.order_by('number'):
            plans = chapter.session_scenes[session.number-1] if chapter.session_scenes else []
            contract = scene_contract(chapter, session.number, plans)
            result = validate_scene(session.content, contract, previous)
            reports.append({'session': session.number, 'stored_status': session.status,
                'mechanical_validation': result.to_dict(),
                'semantic_review': 'NOT_RUN', 'approved': False})
            previous += '\n\n'+session.content
        payload = {'chapter_id':chapter.pk, 'scope':'mechanical_only',
            'manuscript_modified':False, 'model_calls':0, 'sessions':reports}
        write_new(Path(options['output']), payload)
        self.stdout.write(json.dumps({'sessions':len(reports),
            'mechanically_passed':sum(r['mechanical_validation']['passed'] for r in reports),
            'output': options['output']},ensure_ascii=False))
