from django.db import models
from django.db.models import Q


class CorpusSource(models.Model):
    work = models.ForeignKey("editorial.Work", on_delete=models.PROTECT, related_name="corpus_sources")
    provider = models.CharField(max_length=32)
    provider_file_id = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=500)
    raw_path = models.TextField()
    representation = models.CharField(max_length=40, default="drive-text-lf-v1")
    sha256 = models.CharField(max_length=64)
    canonical_for_rag = models.BooleanField(default=False)
    provenance = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("work", "representation", "sha256"), name="corpus_source_content"),
            models.UniqueConstraint(fields=("work",), condition=Q(canonical_for_rag=True), name="corpus_one_canonical_source"),
        ]


class NormalizedArtifact(models.Model):
    source = models.ForeignKey(CorpusSource, on_delete=models.PROTECT, related_name="normalized_artifacts")
    work = models.ForeignKey("editorial.Work", on_delete=models.PROTECT)
    source_sha256 = models.CharField(max_length=64)
    normalized_sha256 = models.CharField(max_length=64)
    normalizer_version = models.CharField(max_length=40)
    normalized_path = models.TextField()
    normalization_report = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("source", "source_sha256", "normalizer_version"), name="corpus_normalization_identity")]


class CorpusBatch(models.Model):
    code = models.SlugField(unique=True)
    author = models.ForeignKey("editorial.Contributor", on_delete=models.PROTECT)
    status = models.CharField(max_length=32, default="RECONCILED")
    fingerprint = models.CharField(max_length=64, blank=True)
    error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class BatchMember(models.Model):
    batch = models.ForeignKey(CorpusBatch, on_delete=models.PROTECT, related_name="members")
    work = models.ForeignKey("editorial.Work", on_delete=models.PROTECT)
    source = models.ForeignKey(CorpusSource, on_delete=models.PROTECT)
    artifact = models.ForeignKey(NormalizedArtifact, null=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("batch", "work"), name="corpus_batch_work")]


class CorpusChunk(models.Model):
    artifact = models.ForeignKey(NormalizedArtifact, on_delete=models.PROTECT, related_name="chunks")
    work = models.ForeignKey("editorial.Work", on_delete=models.PROTECT)
    author = models.ForeignKey("editorial.Contributor", on_delete=models.PROTECT)
    source = models.ForeignKey(CorpusSource, on_delete=models.PROTECT)
    story_or_section = models.CharField(max_length=500)
    chunk_index = models.PositiveIntegerField()
    chunk_id = models.CharField(max_length=64, unique=True)
    text = models.TextField()
    text_sha256 = models.CharField(max_length=64)
    language = models.CharField(max_length=24)
    strategy_version = models.CharField(max_length=40)

    class Meta:
        ordering = ("work_id", "chunk_index")
        constraints = [models.UniqueConstraint(fields=("artifact", "strategy_version", "chunk_index"), name="corpus_chunk_order")]


class VectorizedWork(models.Model):
    artifact = models.ForeignKey(NormalizedArtifact, on_delete=models.PROTECT, related_name="vectorizations")
    fingerprint = models.CharField(max_length=64)
    status = models.CharField(max_length=32, default="VECTOR_READY")
    embedding_model = models.CharField(max_length=200)
    model_digest = models.CharField(max_length=64)
    embedding_dimension = models.PositiveIntegerField()
    embedding_count = models.PositiveIntegerField()
    chunk_count = models.PositiveIntegerField()
    chunk_strategy_version = models.CharField(max_length=40)
    index_path = models.TextField()
    index_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("artifact", "fingerprint"), name="corpus_vector_identity")]


class ProjectCorpus(models.Model):
    project = models.OneToOneField("writer.StoryProject", on_delete=models.CASCADE, related_name="canonical_corpus")
    backend = models.CharField(max_length=32, choices=(("LEGACY_WRITER", "Legacy Writer"), ("CANONICAL_CORPUS", "Canonical corpus")), default="LEGACY_WRITER")
    works = models.ManyToManyField("editorial.Work", blank=True)
