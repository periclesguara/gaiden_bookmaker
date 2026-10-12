class AuthorStudioError(Exception):
    """Base error safe to present in the Author Studio UI."""


class DuplicateAuthorError(AuthorStudioError):
    pass


class DuplicateWorkError(AuthorStudioError):
    pass


class DuplicateSourceError(AuthorStudioError):
    pass


class InvalidSourceError(AuthorStudioError):
    pass


class ExtractionError(AuthorStudioError):
    pass
