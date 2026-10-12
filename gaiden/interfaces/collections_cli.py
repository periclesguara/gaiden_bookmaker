from __future__ import annotations

import argparse
import json
import os
import sys

from gaiden.infrastructure import collections_storage, storage


def _bootstrap_django() -> None:
    web_root = storage.repo_root() / "web"
    if str(web_root) not in sys.path:
        sys.path.insert(0, str(web_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gaiden_portal.settings_sqlite")
    import django

    django.setup()


def _get_collection_by_code(code: str):
    _bootstrap_django()
    from collections_module.models import Collection

    return Collection.objects.get(code=code)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gaiden-collections")
    sub = parser.add_subparsers(dest="command", required=True)

    help_cmd = sub.add_parser("help")
    help_cmd.set_defaults(func=_cmd_help)

    diagnostics = sub.add_parser("diagnostics")
    diagnostics.add_argument("collection_code")
    diagnostics.add_argument("language")
    diagnostics.set_defaults(func=_cmd_diagnostics)

    manifest = sub.add_parser("manifest")
    manifest.add_argument("collection_code")
    manifest.add_argument("language")
    manifest.set_defaults(func=_cmd_manifest)

    create = sub.add_parser("create")
    create.add_argument("--title", required=True)
    create.add_argument("--subtitle", default="")
    create.add_argument("--collection-kind", required=True)
    create.add_argument("--author-display-name", required=True)
    create.add_argument("--language", required=True)
    create.add_argument("--item-count", required=True, type=int)
    create.set_defaults(func=_cmd_create)

    add_item = sub.add_parser("add-item")
    add_item.add_argument("collection_code")
    add_item.add_argument("--order-index", required=True, type=int)
    add_item.add_argument("--author-name", required=True)
    add_item.add_argument("--work-title", required=True)
    add_item.set_defaults(func=_cmd_add_item)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("collection_code")
    prepare.set_defaults(func=_cmd_prepare)

    normalize = sub.add_parser("normalize")
    normalize.add_argument("collection_code")
    normalize.set_defaults(func=_cmd_normalize)

    merge = sub.add_parser("merge")
    merge.add_argument("collection_code")
    merge.set_defaults(func=_cmd_merge)

    ready = sub.add_parser("ready")
    ready.add_argument("collection_code")
    ready.set_defaults(func=_cmd_ready)

    handoff = sub.add_parser("handoff")
    handoff.add_argument("collection_code")
    handoff.set_defaults(func=_cmd_handoff)
    return parser


def _cmd_help(_: argparse.Namespace) -> int:
    print("Commands: diagnostics, manifest, create, add-item, prepare, normalize, merge, ready, handoff")
    return 0


def _cmd_diagnostics(args: argparse.Namespace) -> int:
    root = collections_storage.ensure_collection_layout(args.collection_code, args.language)
    print(f"collection_root={root}")
    print(f"uploads={collections_storage.uploads_dir(args.collection_code, args.language)}")
    print(f"prepared={collections_storage.prepared_dir(args.collection_code, args.language)}")
    print(f"normalized_items={collections_storage.normalized_items_dir(args.collection_code, args.language)}")
    print(f"merged={collections_storage.merged_dir(args.collection_code, args.language)}")
    print(f"audit={collections_storage.audit_dir(args.collection_code, args.language)}")
    return 0


def _cmd_manifest(args: argparse.Namespace) -> int:
    path = collections_storage.manifest_path(args.collection_code, args.language)
    if not path.exists():
        raise SystemExit(f"Manifest not found: {path}")
    print(json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
    return 0


def _cmd_create(args: argparse.Namespace) -> int:
    _bootstrap_django()
    from collections_module.services import workflow

    collection = workflow.create_collection(
        title=args.title,
        subtitle=args.subtitle,
        collection_kind=args.collection_kind,
        author_display_name=args.author_display_name,
        language=args.language,
        item_count=args.item_count,
    )
    print(collection.code)
    return 0


def _cmd_add_item(args: argparse.Namespace) -> int:
    _bootstrap_django()
    from collections_module.models import CollectionItem
    from collections_module.services import workflow

    collection = _get_collection_by_code(args.collection_code)
    item = CollectionItem.objects.create(
        collection=collection,
        order_index=args.order_index,
        author_name=args.author_name,
        work_title=args.work_title,
    )
    workflow.register_items(collection)
    print(f"{collection.code}#{item.order_index}")
    return 0


def _cmd_prepare(args: argparse.Namespace) -> int:
    _bootstrap_django()
    from collections_module.services import workflow

    collection = _get_collection_by_code(args.collection_code)
    results = workflow.run_prepare(collection)
    print(f"prepared_items={len(results)}")
    return 0


def _cmd_normalize(args: argparse.Namespace) -> int:
    _bootstrap_django()
    from collections_module.services import workflow

    collection = _get_collection_by_code(args.collection_code)
    results = workflow.run_normalize(collection)
    print(f"normalized_items={len(results)}")
    return 0


def _cmd_merge(args: argparse.Namespace) -> int:
    _bootstrap_django()
    from collections_module.services import workflow

    collection = _get_collection_by_code(args.collection_code)
    merged = workflow.run_merge(collection)
    print(merged)
    return 0


def _cmd_ready(args: argparse.Namespace) -> int:
    _bootstrap_django()
    from collections_module.services import workflow
    from gaiden.application.collections import service as collection_service

    collection = _get_collection_by_code(args.collection_code)
    collection_service.mark_ready_for_pipeline(collection)
    context = workflow.build_collection_context(collection)
    print(f"status={context['collection'].status}")
    return 0


def _cmd_handoff(args: argparse.Namespace) -> int:
    _bootstrap_django()
    from collections_module.services import workflow

    collection = _get_collection_by_code(args.collection_code)
    edition = workflow.handoff_to_pipeline(collection)
    print(f"edition_id={edition.id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
