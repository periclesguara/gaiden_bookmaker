"""Offline scene gates. Literary/semantic approval belongs to the local critic."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
from functools import lru_cache
import re

from langdetect import DetectorFactory
from langdetect.detector_factory import PROFILES_DIRECTORY
from langdetect.lang_detect_exception import LangDetectException


class ValidationSeverity(str, Enum):
    WARNING = 'warning'
    ERROR = 'error'
    FATAL = 'fatal'


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: ValidationSeverity
    score_penalty: float = 0


@dataclass
class ValidationResult:
    passed: bool
    score: float
    issues: list[ValidationIssue] = field(default_factory=list)
    scope: str = 'mechanical'

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class SceneContract:
    purpose: str
    required_events: list[str] = field(default_factory=list)
    prohibited_events: list[str] = field(default_factory=list)
    expected_language: str = 'en'
    min_words: int = 700
    max_words: int = 1300
    pov: str = 'FIRST_PERSON_WATSON'
    setting_year: int | None = 1913
    style: str = 'Watson: humane, observational, restrained. Holmes: concise, precise, occasionally sardonic.'
    banned_terms: list[str] = field(default_factory=list)
    allowed_foreign_terms: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.purpose.strip() or not 1 <= self.min_words <= self.max_words <= 12000:
            raise ValueError('Invalid scene contract purpose or word limits')
        if not re.fullmatch(r'[a-z]{2}(?:-[a-z]{2})?', self.expected_language):
            raise ValueError('Use an ISO language code in the scene contract')
        for values in (self.required_events, self.prohibited_events, self.banned_terms, self.allowed_foreign_terms):
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError('Contract lists must contain nonempty strings')


BANNED_MODERN_TERMS = [
    'information theory', 'information warfare', 'fake news', 'algorithm',
    'data flow', 'network effect', 'media ecosystem', 'systemic risk',
    'market signal', 'machine learning', 'artificial intelligence', 'digital',
    'database', 'big data',
]
ALLOWED_FRENCH_TERMS = ['Monsieur', 'Madame', 'Mademoiselle', 'Rue', 'Boulevard',
    'Gare du Nord', 'Grand Palais', 'Place de la Madeleine', 'Rue Laffitte',
    'Paris', 'Montmartre', 'Le Havre']
ABSTRACT_NOUNS = set('manipulation information systems operations networks distribution communication infrastructure optimization strategy methodology framework architecture process analysis'.split())


def tokenize(text):
    return re.findall(r"\b\w+(?:['’\-]\w+)*\b", text.casefold())


def count_words(text):
    return len(tokenize(text))


def split_sentences(text):
    # Protect common abbreviations/initials and include closing quotation marks.
    protected = re.sub(r'\b(?:Mr|Mrs|Ms|Dr|Prof|St|M|Mme|Mlle)\.', lambda m: m[0][:-1]+'\u2024', text)
    protected = re.sub(
        r'\b([A-Z])\.(?=\s+[A-Z])', lambda m: m[1]+'\u2024', protected)
    return [s.replace('\u2024', '.').strip() for s in re.split(r'(?<=[.!?])(?:["”’]+)?\s+|\n+', protected) if s.strip()]


@lru_cache(maxsize=1)
def _detector_factory():
    factory = DetectorFactory()
    factory.load_profile(PROFILES_DIRECTORY)
    factory.seed = 0
    return factory


def detect_language(text):
    try:
        detector = _detector_factory().create()
        detector.append(text)
        best = detector.get_probabilities()[0]
        return best.lang, best.prob
    except (LangDetectException, IndexError):
        return 'unknown', 0.0


def result_from_issues(issues, *, scope='mechanical'):
    score = max(0.0, 100.0 - sum(i.score_penalty for i in issues))
    return ValidationResult(not any(i.severity != ValidationSeverity.WARNING for i in issues) and score >= 75,
                            score, issues, scope)


def validate_language(text, contract):
    cleaned = text
    for term in sorted(contract.allowed_foreign_terms, key=len, reverse=True):
        cleaned = re.sub(r'(?<!\w)'+re.escape(term)+r'(?!\w)', ' ', cleaned, flags=re.I)
    language, confidence = detect_language(cleaned)
    issues = []
    if language != contract.expected_language or confidence < .80:
        return [ValidationIssue('LANGUAGE_MISMATCH', f'Expected {contract.expected_language}; detected {language} ({confidence:.2f}).', ValidationSeverity.FATAL, 100)]
    total = count_words(cleaned)
    foreign = 0
    for segment in split_sentences(cleaned):
        size = count_words(segment)
        if size < 8:
            continue  # Names and very short dialogue are not reliable language evidence.
        detected, probability = detect_language(segment)
        if detected != contract.expected_language and probability >= .80:
            foreign += size
    if total and foreign / total > .08:
        issues.append(ValidationIssue('EXCESSIVE_LANGUAGE_MIXING', f'Confident foreign-language words: {foreign / total:.1%}.', ValidationSeverity.ERROR, 30))
    return issues


def _unbalanced_quotes(text):
    # Repeated opening quote at a paragraph start is conventional continued speech.
    opened = False
    for paragraph in re.split(r'\n\s*\n', text):
        for index, char in enumerate(paragraph.strip()):
            if char in '“"':
                if opened and index == 0:
                    continue
                if char == '“':
                    if opened:
                        return True
                    opened = True
                else:
                    opened = not opened
            elif char == '”':
                if not opened:
                    return True
                opened = False
    return opened


def validate_completion(text, finish_reason='stop'):
    issues = []
    stripped = text.strip()
    if not stripped:
        return [ValidationIssue('EMPTY_SCENE', 'Scene is empty.', ValidationSeverity.FATAL, 100)]
    if finish_reason != 'stop':
        issues.append(ValidationIssue('TRUNCATED_RESPONSE', f'Model finish_reason={finish_reason}; complete stop required.', ValidationSeverity.FATAL, 100))
    if not re.search(r'[.!?]["”’\')\]]*$', stripped):
        issues.append(ValidationIssue('INCOMPLETE_ENDING', 'Narrative does not end with a complete sentence.', ValidationSeverity.FATAL, 100))
    if _unbalanced_quotes(stripped):
        issues.append(ValidationIssue('UNCLOSED_QUOTE', 'Unbalanced dialogue quotation marks.', ValidationSeverity.FATAL, 100))
    # A short but punctuated final line ("Come, Watson.") is not proof of truncation.
    return issues


def get_ngrams(text, n=8):
    words = tokenize(text)
    return {tuple(words[i:i+n]) for i in range(len(words)-n+1)}


def validate_scene(text, contract, previous_text=None, *, finish_reason='stop', sources=()):
    issues = validate_completion(text, finish_reason)
    if not text.strip():
        return result_from_issues(issues)
    issues += validate_language(text, contract)
    words = tokenize(text)
    size = len(words)
    if size < contract.min_words or size > contract.max_words:
        issues.append(ValidationIssue('SCENE_TOO_SHORT' if size < contract.min_words else 'SCENE_TOO_LONG',
            f'{size} words; contract requires {contract.min_words}–{contract.max_words}.', ValidationSeverity.ERROR, 25))
    for index, sentence in enumerate(split_sentences(text), 1):
        n = count_words(sentence)
        if n > 60:
            severity = ValidationSeverity.FATAL if n > 120 else ValidationSeverity.ERROR if n > 90 else ValidationSeverity.WARNING
            issues.append(ValidationIssue('RUNAWAY_SENTENCE' if n > 90 else 'LONG_SENTENCE', f'Sentence {index}: {n} words.', severity, 100 if n > 120 else 30 if n > 90 else 5))
    longest = max((count_words(c) for c in re.split(r'[.!?,;:—]', text)), default=0)
    if longest > 100 or (size > 150 and sum(text.count(c) for c in '.,;:!?')/size < .015):
        issues.append(ValidationIssue('DEGENERATE_GENERATION', 'Abnormally long unpunctuated word chain.', ValidationSeverity.FATAL, 100))
    if size > 100:
        token, frequency = Counter(words).most_common(1)[0]
        if frequency/size > .10:
            issues.append(ValidationIssue('TOKEN_REPETITION_SPIKE', f'A token occupies {frequency/size:.1%} of the scene.', ValidationSeverity.ERROR, 25))
        ngrams = [tuple(words[i:i+8]) for i in range(size-7)]
        if ngrams and 1-len(set(ngrams))/len(ngrams) >= .20:
            issues.append(ValidationIssue('INTERNAL_REPETITION', 'At least 20% of 8-word phrases are repeated within the scene.', ValidationSeverity.ERROR, 30))
    if previous_text:
        current = get_ngrams(text)
        overlap = len(current & get_ngrams(previous_text))/max(1,len(current))
        if overlap >= .10:
            issues.append(ValidationIssue('HIGH_PHRASE_REPETITION' if overlap >= .20 else 'POSSIBLE_PHRASE_REPETITION',
                f'8-gram overlap with prior sessions: {overlap:.1%}.', ValidationSeverity.ERROR if overlap >= .20 else ValidationSeverity.WARNING, 30 if overlap >= .20 else 10))
        if get_ngrams(text,40) & get_ngrams(previous_text,40):
            issues.append(ValidationIssue('EXACT_SESSION_REPETITION', '40 consecutive words copied from previous sessions.', ValidationSeverity.ERROR, 30))
    if any(get_ngrams(text,14) & get_ngrams(source,14) for source in sources):
        issues.append(ValidationIssue('SOURCE_COPY', '14 consecutive words copied from retrieved corpus.', ValidationSeverity.ERROR, 30))
    if contract.pov == 'FIRST_PERSON_WATSON':
        if re.search(r"\bHolmes (?:thought|realized|knew|understood|secretly)\b|\bin Holmes['’]?s? mind\b",text,re.I):
            issues.append(ValidationIssue('POV_VIOLATION', 'Possible access to Holmes’s private thoughts; critic must check context.', ValidationSeverity.WARNING,10))
        if size > 500 and len(re.findall(r'\b(?:I|we|my|our|us)\b',text,re.I)) < 3:
            issues.append(ValidationIssue('POV_DRIFT','Insufficient first-person narration.',ValidationSeverity.ERROR,20))
    for term in contract.banned_terms:
        if re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)', text, re.I):
            issues.append(ValidationIssue('PROHIBITED_VOCABULARY',f'Editorially prohibited term: {term}.',ValidationSeverity.ERROR,20))
    for index, dialogue in enumerate(re.findall(r'"([^"\n]+)"|“([^”]+)”',text),1):
        phrase = next((p for p in dialogue if p),'')
        if count_words(phrase)>70 or len(set(tokenize(phrase)) & ABSTRACT_NOUNS)>=5:
            issues.append(ValidationIssue('DIALOGUE_EXPOSITION',f'Dialogue {index} needs review for excessive exposition.',ValidationSeverity.WARNING,8))
    return result_from_issues(issues)
