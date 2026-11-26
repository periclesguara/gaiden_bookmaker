import posixpath
import re
from zipfile import ZipFile

from .epub_preview import member_path, package, parse_xml, read_member


def chapter_illustrations(epub_path):
    pages, members = package(epub_path)
    illustrations = {}
    chapter_index = 0
    with ZipFile(epub_path) as archive:
        for page in pages:
            member = page['path']
            document = parse_xml(read_member(archive, member))
            sections = [node for node in document.iter()
                        if 'chapter' in node.get('{http://www.idpf.org/2007/ops}type', '').split()]
            filename_match = re.fullmatch(r'chapter_(\d+)(?:_opening)?\.xhtml', posixpath.basename(member))
            if not sections and filename_match:
                sections = [document]
            for section in sections:
                identifier = re.fullmatch(r'chapter[-_](\d+)(?:-body)?', section.get('id', ''))
                if identifier or filename_match:
                    chapter_number = int((identifier or filename_match).group(1))
                    chapter_index = max(chapter_index, chapter_number)
                else:
                    chapter_index += 1
                    chapter_number = chapter_index
                for image in section.findall('.//{*}img'):
                    href = image.get('src')
                    if not href:
                        raise ValueError('Ilustração sem recurso no EPUB aprovado.')
                    image_member = member_path(posixpath.dirname(member), href)
                    if image_member not in members or not members[image_member].startswith('image/'):
                        raise ValueError('Ilustração ausente do manifesto do EPUB aprovado.')
                    if chapter_number in illustrations:
                        raise ValueError(f'Capítulo {chapter_number}: múltiplas ilustrações exigem configuração própria no PrintBook.')
                    illustrations[chapter_number] = image_member
    return illustrations
