from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def _attr(element: ET.Element, name: str) -> str:
    return element.attrib.get(name, "").strip()


def _join_href(base_dir: str, href: str) -> str:
    return posixpath.normpath(posixpath.join(base_dir, href))


@dataclass(frozen=True)
class EpubPackage:
    opf_path: str
    opf_dir: str
    metadata: dict
    manifest: dict[str, dict]
    spine: list[dict]
    toc: list[dict]


class EpubReader:
    def __init__(self, epub_path: str | Path):
        self.epub_path = Path(epub_path)

    def read(self) -> EpubPackage:
        with zipfile.ZipFile(self.epub_path, "r") as zf:
            opf_path = self._find_opf_path(zf)
            opf_root = ET.fromstring(zf.read(opf_path))
            opf_dir = posixpath.dirname(opf_path)
            metadata = self._parse_metadata(opf_root)
            manifest = self._parse_manifest(opf_root, opf_dir)
            spine = self._parse_spine(opf_root, manifest)
            toc = self._parse_toc(zf, opf_root, manifest, opf_dir)
            return EpubPackage(
                opf_path=opf_path,
                opf_dir=opf_dir,
                metadata=metadata,
                manifest=manifest,
                spine=spine,
                toc=toc,
            )

    def _find_opf_path(self, zf: zipfile.ZipFile) -> str:
        try:
            container_raw = zf.read("META-INF/container.xml")
        except KeyError as exc:
            raise ValueError("Invalid EPUB: META-INF/container.xml not found.") from exc
        root = ET.fromstring(container_raw)
        for element in root.iter():
            if _local_name(element.tag) == "rootfile":
                full_path = _attr(element, "full-path")
                if full_path:
                    return full_path
        raise ValueError("Invalid EPUB: content.opf rootfile not found in container.xml.")

    def _parse_metadata(self, opf_root: ET.Element) -> dict:
        metadata_node = next((child for child in opf_root if _local_name(child.tag) == "metadata"), None)
        values = {
            "title": "",
            "creators": [],
            "languages": [],
            "publisher": "",
            "rights": "",
            "date": "",
            "identifier": "",
        }
        if metadata_node is None:
            return values
        for child in metadata_node:
            name = _local_name(child.tag)
            value = _text(child)
            if not value:
                continue
            if name == "title" and not values["title"]:
                values["title"] = value
            elif name == "creator":
                values["creators"].append(value)
            elif name == "language":
                values["languages"].append(value)
            elif name in {"publisher", "rights", "date", "identifier"} and not values[name]:
                values[name] = value
        return values

    def _parse_manifest(self, opf_root: ET.Element, opf_dir: str) -> dict[str, dict]:
        manifest_node = next((child for child in opf_root if _local_name(child.tag) == "manifest"), None)
        manifest: dict[str, dict] = {}
        if manifest_node is None:
            return manifest
        for item in manifest_node:
            if _local_name(item.tag) != "item":
                continue
            item_id = _attr(item, "id")
            href = _attr(item, "href")
            if not item_id or not href:
                continue
            manifest[item_id] = {
                "id": item_id,
                "href": href,
                "path": _join_href(opf_dir, href),
                "media_type": _attr(item, "media-type"),
                "properties": _attr(item, "properties"),
            }
        return manifest

    def _parse_spine(self, opf_root: ET.Element, manifest: dict[str, dict]) -> list[dict]:
        spine_node = next((child for child in opf_root if _local_name(child.tag) == "spine"), None)
        spine: list[dict] = []
        if spine_node is None:
            return spine
        for itemref in spine_node:
            if _local_name(itemref.tag) != "itemref":
                continue
            idref = _attr(itemref, "idref")
            if idref in manifest:
                spine.append(manifest[idref])
        return spine

    def _parse_toc(self, zf: zipfile.ZipFile, opf_root: ET.Element, manifest: dict[str, dict], opf_dir: str) -> list[dict]:
        nav_item = next((item for item in manifest.values() if "nav" in item.get("properties", "").split()), None)
        if nav_item:
            return self._parse_nav_toc(zf, nav_item["path"])

        spine_node = next((child for child in opf_root if _local_name(child.tag) == "spine"), None)
        toc_id = _attr(spine_node, "toc") if spine_node is not None else ""
        ncx_item = manifest.get(toc_id) if toc_id else None
        if ncx_item is None:
            ncx_item = next((item for item in manifest.values() if item.get("media_type") == "application/x-dtbncx+xml"), None)
        if ncx_item:
            return self._parse_ncx_toc(zf, ncx_item["path"])
        return []

    def _parse_nav_toc(self, zf: zipfile.ZipFile, nav_path: str) -> list[dict]:
        try:
            root = ET.fromstring(zf.read(nav_path))
        except Exception:
            return []
        entries: list[dict] = []
        in_toc = False
        for element in root.iter():
            name = _local_name(element.tag)
            if name == "nav":
                epub_type = element.attrib.get("{http://www.idpf.org/2007/ops}type", "") or _attr(element, "type")
                in_toc = "toc" in epub_type.split()
            if in_toc and name == "a":
                label = _text(element)
                href = _attr(element, "href")
                if label or href:
                    entries.append({"label": label, "href": href})
        return entries

    def _parse_ncx_toc(self, zf: zipfile.ZipFile, ncx_path: str) -> list[dict]:
        try:
            root = ET.fromstring(zf.read(ncx_path))
        except Exception:
            return []
        entries: list[dict] = []
        for nav_point in root.iter():
            if _local_name(nav_point.tag) != "navPoint":
                continue
            label = ""
            href = ""
            for child in nav_point.iter():
                name = _local_name(child.tag)
                if name == "text" and not label:
                    label = _text(child)
                elif name == "content" and not href:
                    href = _attr(child, "src")
            entries.append({"label": label, "href": href})
        return entries
