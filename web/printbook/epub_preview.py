"""Read approved EPUB members without extracting or regenerating the book."""
import posixpath
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile
from lxml import etree


def parse_xml(content):
    return etree.fromstring(content, parser=etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))

MAX_MEMBER_BYTES = 20 * 1024 * 1024


def member_path(base, href):
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        raise ValueError("Referência externa não suportada no preview.")
    path = posixpath.normpath(posixpath.join(base, unquote(parsed.path)))
    if path.startswith(("/", "../")) or path == ".." or "\\" in path:
        raise ValueError("Caminho inválido no EPUB.")
    return path


def read_member(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_MEMBER_BYTES:
        raise ValueError("Recurso do EPUB excede o limite de preview.")
    return archive.read(info)


def package(path):
    with ZipFile(path) as archive:
        container = parse_xml(read_member(archive, "META-INF/container.xml"))
        root = container.find(".//{*}rootfile")
        if root is None:
            raise ValueError("EPUB sem pacote de leitura.")
        opf_path = member_path("", root.attrib["full-path"])
        opf = parse_xml(read_member(archive, opf_path))
        members = {}
        items = {}
        for item in opf.findall("{*}manifest/{*}item"):
            name = member_path(posixpath.dirname(opf_path), item.attrib["href"])
            members[name] = item.attrib["media-type"]
            items[item.attrib["id"]] = name
        pages = []
        for item in opf.findall("{*}spine/{*}itemref"):
            name = items[item.attrib["idref"]]
            if members[name] not in {"application/xhtml+xml", "text/html"}:
                raise ValueError("Página do EPUB não suportada.")
            pages.append({"path": name, "label": f"{len(pages) + 1} · {posixpath.basename(name)}"})
        if not pages:
            raise ValueError("EPUB sem páginas de leitura.")
        return pages, members
