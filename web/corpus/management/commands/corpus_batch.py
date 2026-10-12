import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from corpus.embedding import OllamaEmbedder
from corpus.models import CorpusBatch
from corpus.services import (batch_status, chunk_batch, normalize_batch, preview_manifest,
                             register_manifest, vectorize_batch)


class Command(BaseCommand):
    help = "Preview, reconcile and prepare an approved Work corpus without touching editorial editions."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("preview", "register", "normalize", "chunk", "vectorize", "status"))
        parser.add_argument("--manifest")
        parser.add_argument("--batch")
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--model")
        parser.add_argument("--model-digest")
        parser.add_argument("--dimension", type=int)

    def handle(self, *args, **options):
        try:
            action = options["action"]
            if action in {"preview", "register"}:
                if not options["manifest"]:
                    raise CommandError("--manifest is required")
                manifest = json.loads(Path(options["manifest"]).read_text(encoding="utf-8"))
                preview = preview_manifest(manifest)
                if action == "preview":
                    self.stdout.write(json.dumps(preview))
                    if preview["conflicts"]:
                        raise CommandError("Batch requires review")
                    return
                if not options["confirm"]:
                    raise CommandError("Inspect preview and provide --confirm before registering")
                batch = register_manifest(manifest)
            else:
                batch = CorpusBatch.objects.get(code=options["batch"])
                if action == "normalize":
                    normalize_batch(batch)
                elif action == "chunk":
                    chunk_batch(batch)
                elif action == "vectorize":
                    if not all(options[k] for k in ("model", "model_digest", "dimension")):
                        raise CommandError("Approved model, immutable digest and dimension are required")
                    vectorize_batch(batch, OllamaEmbedder(options["model"], options["model_digest"], options["dimension"]))
            self.stdout.write(json.dumps(batch_status(batch)))
        except (ValueError, OSError, KeyError, CorpusBatch.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
