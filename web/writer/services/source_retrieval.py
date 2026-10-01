from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from gaiden.writer_engine.corpus import SourceChunk, chunk_source
from ..models import ProjectReference, SourceVectorIndex


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
        "source__edition__work__author", "source__edition__language", "source__writer_vector_index"
    ).order_by("source_id"))
    if roles:
        wanted = {role.upper() for role in roles}
        references = [reference for reference in references if wanted.intersection(reference.roles or [])]

    rows: list[tuple[ProjectReference, SourceChunk]] = []

    for reference in references:
        source = reference.source
        try:
            index = source.writer_vector_index
        except SourceVectorIndex.DoesNotExist:
            continue
        if index.status != SourceVectorIndex.Status.READY or index.source_hash != source.official_body_sha256:
            continue
        path = source._safe_existing_path(source.official_body_path)
        if path is None:
            continue

        for chunk in chunk_source(path, path.parent):
            rows.append((reference, chunk))

    return rows


def retrieve_source_rag(
    project,
    chapter,
    *,
    top_k: int = 8,
    max_context_chars: int = 24000,
    roles: list[str] | None = None,
) -> list[dict]:
    """
    Deterministic local source retrieval.

    No embeddings.
    No model calls.
    No external API.

    Ranking is BM25-style lexical relevance over the normalized corpus.
    """
    if not 1 <= top_k <= 50:
        raise ValueError("top_k must be between 1 and 50")

    query = _chapter_query(chapter)
    query_tokens = _tokens(query)

    if not query_tokens:
        return []

    rows = _load_project_chunks(project, roles=roles)
    if not rows:
        return []

    query_counts = Counter(query_tokens)

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

        if score > 0:
            scored.append((score, reference, chunk))
        else:
            # Very small fallback score for recognizably Holmes/Watson
            # narrative chunks. These only enter if thematic retrieval
            # cannot fill top_k.
            lowered = chunk.text.casefold()
            style_score = 0.0
            if "holmes" in lowered:
                style_score += 0.08
            if "watson" in lowered:
                style_score += 0.08
            if '" ' in chunk.text or '"' in chunk.text:
                style_score += 0.02

            if style_score:
                scored.append((style_score, reference, chunk))

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
        if per_source[chunk.source_path] >= 2:
            continue

        if selected and used_chars + text_size > max_context_chars:
            continue

        selected.append(
            {
                "retrieval_method": "local_lexical_bm25",
                "source_work_id": reference.source_id,
                "title": reference.source.edition.title or reference.source.edition.work.title,
                "author": reference.source.edition.author or reference.source.edition.work.author.name,
                "source_version": reference.source.build_version,
                "reference_roles": reference.roles,
                "reference_notes": reference.notes,
                "source_path": Path(reference.source.official_body_path).name,
                "heading": chunk.heading,
                "chunk_id": chunk.chunk_id,
                "ordinal": chunk.ordinal,
                "score": round(score, 6),
                "text": chunk.text,
            }
        )

        used_chars += text_size
        per_source[chunk.source_path] += 1

    return selected
