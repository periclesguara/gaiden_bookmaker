from django import forms


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"multiple": True, "webkitdirectory": True}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)] if data else []


class AutomatedEditorialPreviewForm(forms.Form):
    package_file = forms.FileField(
        label="Pacote editorial JSON",
        help_text="Selecione o import-package.json produzido pelo fluxo editorial.",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".json,application/json",
                "data-file-role": "package",
            }
        ),
    )
    manifest_file = forms.FileField(
        label="Manifesto incremental JSON",
        help_text="Selecione o manifest.json correspondente aos blocos.",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".json,application/json",
                "data-file-role": "manifest",
            }
        ),
    )
    artifact_files = MultipleFileField(
        label="Pasta de artefatos e blocos",
        required=True,
        help_text="Selecione a pasta que contém fonte, corpos e todos os blocos citados nos JSONs.",
        widget=MultipleFileInput(
            attrs={
                "multiple": True,
                "webkitdirectory": True,
                "directory": True,
                "data-file-role": "artifacts",
            }
        ),
    )
    drive_destination = forms.CharField(
        label="Destino Google Drive/rclone após confirmação",
        max_length=2000,
        required=False,
        help_text=(
            "Opcional. Use o diretório return do job, por exemplo "
            "gaiden_drive:04_TRANSLATION_JOBS/book_XXXX/pt-br/return. "
            "Nenhum acesso ao Drive é feito durante a prévia."
        ),
        widget=forms.TextInput(
            attrs={
                "placeholder": "gaiden_drive:04_TRANSLATION_JOBS/book_XXXX/pt-br/return",
                "autocomplete": "off",
            }
        ),
    )


class AutomatedEditorialConfirmForm(forms.Form):
    preview_token = forms.CharField(widget=forms.HiddenInput)
    drive_destination = forms.CharField(widget=forms.HiddenInput, required=False)


class DriveFolderPreviewForm(forms.Form):
    folder_path = forms.CharField(
        label="Pasta existente dentro de 01_INBOX_RAW",
        max_length=1000,
        help_text="Ex.: pasta_do_lote. O caminho original será preservado.",
    )
    batch_name = forms.CharField(label="Nome do lote", max_length=255)
    default_author = forms.CharField(label="Autor padrão", max_length=255, required=False)
    source_language = forms.CharField(label="Idioma de origem", max_length=16, initial="en")
    target_language = forms.CharField(label="Idioma de destino", max_length=16, required=False)
    seal = forms.CharField(label="Selo", max_length=150, required=False)
    recursive = forms.BooleanField(label="Incluir subpastas", required=False, initial=True)


class DriveFolderConfirmForm(forms.Form):
    preview_token = forms.CharField(widget=forms.HiddenInput)


class DriveFolderRetryForm(forms.Form):
    batch_code = forms.RegexField(regex=r"^batch_[0-9]{4,}$", max_length=32)


class IncrementalImportForm(forms.Form):
    manifest_path = forms.CharField(
        label="Caminho do manifest.json",
        max_length=2000,
        help_text="Caminho local do manifesto incremental no servidor Gaiden.",
    )
    blocks_directory = forms.CharField(
        label="Pasta dos blocos",
        max_length=2000,
        required=False,
        help_text="Opcional; o Gaiden também procura ao lado do manifesto e em ../blocks.",
    )
    drive_destination = forms.CharField(
        label="Destino para reenvio",
        max_length=2000,
        required=False,
        help_text=(
            "Pasta local/sincronizada ou remoto rclone, por exemplo "
            "gaiden_drive:Gaiden Bookmaker/04_TRANSLATION_JOBS/book_XXXX/pt-br/return."
        ),
    )
    import_attempt = forms.IntegerField(
        label="Tentativa de importação",
        min_value=1,
        initial=1,
    )
    stop_on_conflict = forms.BooleanField(
        label="Interromper no primeiro conflito",
        required=False,
        initial=True,
    )
