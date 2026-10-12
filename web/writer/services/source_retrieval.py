from __future__ import annotations

import math
import re
from collections import Counter

from gaiden.writer_engine.corpus import SourceChunk
from gaiden.writer_engine.index import VectorIndex
from ..models import ProjectReference
from .corpus import index_ready, reference_index, safe_path


TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)

PT_EN_TERMS = {
    # investigação
    "investigação": ("investigation", "inquiry"),
    "investigar": ("investigate", "inquiry"),
    "detetive": ("detective",),
    "pista": ("clue", "evidence"),
    "prova": ("evidence", "proof"),
    "documento": ("document", "paper"),
    "documentos": ("documents", "papers"),
    "carta": ("letter",),
    "cartas": ("letters",),
    "telegrama": ("telegram",),
    "telegramas": ("telegrams",),
    "fotografia": ("photograph", "photography"),
    "foto": ("photograph",),
    "vigilância": ("surveillance", "watching"),
    "seguir": ("follow", "shadow"),
    "seguidor": ("follower", "watcher"),
    "identidade": ("identity", "name"),
    "nome": ("name", "identity"),

    # crime / pressão
    "chantagem": ("blackmail", "blackmailer"),
    "ameaça": ("threat", "danger"),
    "ameaças": ("threats", "danger"),
    "crime": ("crime", "criminal"),
    "criminoso": ("criminal",),
    "medo": ("fear", "terror"),
    "terror": ("terror", "fear"),
    "suicídio": ("suicide",),
    "morte": ("death",),
    "segredo": ("secret",),
    "segredos": ("secrets",),

    # finanças
    "banco": ("bank", "banking"),
    "bancos": ("banks", "banking"),
    "banqueiro": ("banker",),
    "banqueiros": ("bankers",),
    "financeiro": ("financial", "finance"),
    "financeira": ("financial", "finance"),
    "dinheiro": ("money", "capital"),
    "capital": ("capital", "money"),
    "mercado": ("market",),
    "bolsa": ("exchange", "market"),
    "ações": ("shares", "stocks"),
    "dívida": ("debt",),
    "dívidas": ("debts",),
    "crédito": ("credit",),
    "garantia": ("guarantee", "security"),
    "garantias": ("guarantees", "securities"),
    "ruína": ("ruin", "bankruptcy"),
    "falência": ("bankruptcy", "insolvency"),
    "empréstimo": ("loan",),
    "resgate": ("rescue", "salvation"),
    "fortuna": ("fortune", "wealth"),
    "riqueza": ("wealth", "fortune"),

    # ambiente / personagens
    "hotel": ("hotel",),
    "paris": ("paris",),
    "frança": ("france", "french"),
    "francês": ("french",),
    "alemão": ("german", "germany"),
    "alemanha": ("germany", "german"),
    "londres": ("london",),
    "holmes": ("holmes",),
    "watson": ("watson",),
    "homem": ("man",),
    "mulher": ("woman",),
    "escritório": ("office",),
    "rua": ("street",),
}

STOPWORDS = {
    # English
    "the", "and", "that", "this", "with", "from", "into", "upon", "were",
    "was", "are", "for", "had", "has", "have", "his", "her", "their", "our",
    "but", "not", "you", "your", "they", "them", "then", "than", "when",
    "where", "which", "who", "what", "would", "could", "should", "there",
    "here", "about", "after", "before", "through", "while", "only", "very",
    "one", "two", "out", "all", "any", "more", "some",

    # Portuguese — useful because directions/scripts may be in PT-BR
    "que", "para", "com", "uma", "por", "como", "dos", "das", "este",
    "esta", "isso", "ele", "ela", "eles", "elas", "seu", "sua", "seus",
    "suas", "não", "mais", "menos", "deve", "dever", "entre", "sobre",
    "após", "antes", "quando", "onde", "ainda", "cada", "capítulo",
    "cena", "cenas",
}


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in TOKEN_RE.findall((text or "").casefold())
        if len(token) >= 3 and token not in STOPWORDS
    ]


def _chapter_query(chapter) -> str:
    parts = [
        getattr(chapter, "title", ""),
        getattr(chapter, "direction", ""),
        getattr(chapter, "script", ""),
    ]

    raw = "\n\n".join(
        part.strip()
        for part in parts
        if part and part.strip()
    )

    raw_tokens = _tokens(raw)
    expansions: list[str] = []

    for token in raw_tokens:
        expansions.extend(PT_EN_TERMS.get(token, ()))

    if expansions:
        return raw + "\n\n" + " ".join(expansions)

    return raw


def _load_project_chunks(project, roles=None) -> list[tuple[ProjectReference, SourceChunk]]:
    references = list(project.references.filter(enabled=True).select_related(
        "intake_work__author", "source__edition__work__author", "source__edition__language"
    ).order_by("pk"))
    if roles:
        wanted = {role.upper() for role in roles}
        references = [reference for reference in references if wanted.intersection(reference.roles or [])]
    rows = []
    for reference in references:
        index = reference_index(reference)
        if not index or not index_ready(index):
            continue
        try:
            stored = VectorIndex.load(safe_path(index.index_path))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        rows.extend((reference, chunk) for chunk, _ in stored.rows)
    return rows


def _preferred_authors(project, chapter):
    chapter_terms = set(_tokens(_chapter_query(chapter)))
    preferred = set()
    guide = project.reference_bible or ""
    directives = re.split(r"\n\s*\n|(?<=\.)\s+", guide)
    ignored = {"priorizar", "prioritize", "principal", "reference", "referência", "usar", "como"}
    for reference in project.references.filter(enabled=True).select_related("intake_work__author", "source__edition__work__author"):
        work = reference.intake_work or (reference.source.edition.work if reference.source_id else None)
        if work is None:
            continue
        name = work.author.name
        for directive in directives:
            if name.casefold() not in directive.casefold():
                continue
            topics = set(_tokens(directive)) - set(_tokens(name)) - ignored
            if chapter_terms.intersection(topics):
                preferred.add(work.author_id)
    return preferred


def retrieve_source_rag(
    project,
    chapter,
    *,
    top_k: int = 8,
    max_context_chars: int = 24000,
    roles: list[str] | None = None,
) -> list[dict]:
    """
    Legacy lexical retrieval; canonical projects use the local dense backend.

    No embeddings.
    No model calls.
    No external API.

    Ranking is BM25-style lexical relevance over the normalized corpus.
    """
    if not 1 <= top_k <= 50:
        raise ValueError("top_k must be between 1 and 50")

    from corpus.writer_adapter import is_canonical, source_rag
    if is_canonical(project):
        return source_rag(project, chapter, top_k=top_k, max_context_chars=max_context_chars)

    query = _chapter_query(chapter)
    query_tokens = _tokens(query)

    if not query_tokens:
        return []

    rows = _load_project_chunks(project, roles=roles)
    if not rows:
        return []

    query_counts = Counter(query_tokens)
    preferred_authors = _preferred_authors(project, chapter)

    document_tokens: list[list[str]] = []
    document_counts: list[Counter] = []

    for _, chunk in rows:
        # Heading gets extra influence without needing a separate model.
        tokens = (
            _tokens(chunk.heading) * 3
            + _tokens(chunk.text)
        )
        document_tokens.append(tokens)
        document_counts.append(Counter(tokens))

    total_docs = len(rows)
    avg_length = (
        sum(len(tokens) for tokens in document_tokens) / total_docs
        if total_docs
        else 1.0
    )

    df: Counter = Counter()
    for counts in document_counts:
        for term in query_counts:
            if counts.get(term, 0):
                df[term] += 1

    k1 = 1.5
    b = 0.75

    scored: list[tuple[float, ProjectReference, SourceChunk]] = []

    for idx, (reference, chunk) in enumerate(rows):
        counts = document_counts[idx]
        length = max(len(document_tokens[idx]), 1)
        score = 0.0

        for term, query_frequency in query_counts.items():
            tf = counts.get(term, 0)
            if not tf:
                continue

            term_df = df.get(term, 0)
            idf = math.log(
                1.0
                + (total_docs - term_df + 0.5)
                / (term_df + 0.5)
            )

            denominator = tf + k1 * (
                1.0 - b + b * (length / avg_length)
            )

            bm25 = idf * (
                tf * (k1 + 1.0)
            ) / denominator

            # Repeated terms in the chapter plan matter,
            # but cap the influence to avoid pathological repetition.
            query_weight = min(1.0 + math.log(query_frequency), 3.0)
            score += bm25 * query_weight

        work = reference.intake_work or reference.source.edition.work
        if work.author_id in preferred_authors:
            score *= 2
        if score > 0:
            scored.append((score, reference, chunk))
    scored.sort(
        key=lambda item: (
            -item[0],
            item[2].source_path,
            item[2].ordinal,
        )
    )

    selected: list[dict] = []
    used_chars = 0
    per_source: Counter = Counter()

    for score, reference, chunk in scored:
        if len(selected) >= top_k:
            break

        text_size = len(chunk.text)

        # Ignore headings, fragments and other chunks too small
        # to provide meaningful literary reference.
        if text_size < 300:
            continue

        # Avoid letting one Doyle volume dominate the entire context.
        work = reference.intake_work or reference.source.edition.work
        if per_source[work.pk] >= 2:
            continue

        if used_chars + text_size > max_context_chars:
            continue

        selected.append(
            {
                "retrieval_method": "local_lexical_bm25",
                "source_work_id": work.pk,
                "canonical_work_id": work.pk,
                "source_build_id": reference.source_id,
                "title": work.title,
                "author": work.author.name,
                "source_version": reference.source.build_version if reference.source_id else 1,
                "reference_roles": reference.roles,
                "reference_notes": reference.notes,
                "source_path": chunk.source_path,
                "heading": chunk.heading,
                "chunk_id": chunk.chunk_id,
                "ordinal": chunk.ordinal,
                "score": round(score, 6),
                "text": chunk.text,
            }
        )

        used_chars += text_size
        per_source[work.pk] += 1

    return selected
