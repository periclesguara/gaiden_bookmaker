from django import forms
from editorial.models import EditionBuild
from gaiden.print_engine.isbn import isbn13
from .models import PrintEdition
from .services import spec_for
from .catalog import catalog, eligible


class EditionForm(forms.ModelForm):
    class Meta:
        model = PrintEdition
        fields = ["source", "name", "trim", "paper", "typography", "isbn", "barcode_mode"]
        labels = {"source": "Build editorial final aprovado", "name": "Nome da edição física", "trim": "Formato", "paper": "Papel", "typography": "Perfil tipográfico", "isbn": "ISBN exclusivo desta edição física", "barcode_mode": "Código de barras"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].queryset = EditionBuild.objects.filter(pk__in=[source.pk for source in catalog()])

    def clean_source(self):
        source = self.cleaned_data["source"]
        if not eligible(source):
            raise forms.ValidationError("Build editorial sem aprovação/integridade atual.")
        return source

    def clean_isbn(self):
        value = self.cleaned_data.get("isbn")
        try:
            return isbn13(value) if value else None
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean(self):
        data = super().clean()
        if all(data.get(key) for key in ("trim", "paper", "typography")):
            candidate = PrintEdition(trim=data["trim"], paper=data["paper"], typography=data["typography"])
            try:
                spec_for(candidate)
            except ValueError as exc:
                raise forms.ValidationError(str(exc)) from exc
        return data


class EditionConfigurationForm(EditionForm):
    """Editable print settings; the approved editorial source is immutable."""

    class Meta(EditionForm.Meta):
        fields = ["name", "trim", "paper", "typography", "isbn", "barcode_mode"]

    def __init__(self, *args, **kwargs):
        forms.ModelForm.__init__(self, *args, **kwargs)

    def clean(self):
        data = forms.ModelForm.clean(self)
        if all(data.get(key) for key in ("trim", "paper", "typography")):
            candidate = PrintEdition(
                trim=data["trim"],
                paper=data["paper"],
                typography=data["typography"],
            )
            try:
                spec_for(candidate)
            except ValueError as exc:
                raise forms.ValidationError(str(exc)) from exc
        return data


class CoverForm(forms.Form):
    cover = forms.FileField(label="PDF da capa completa")
    template = forms.FileField(label="PDF do template oficial para este miolo")
    spine = forms.DecimalField(label="Lombada indicada no template (polegadas)", min_value=0.001, max_value=5, max_digits=7, decimal_places=4)


class CoverCompositionForm(forms.Form):
    front = forms.FileField(label="Frente em PDF (uma página, com sangria externa)")
    back = forms.FileField(label="Contracapa em PDF (uma página, com sangria externa)")
    template = forms.FileField(label="Template oficial para a paginação final")
    spine = forms.DecimalField(label="Lombada do template (polegadas)", min_value=0.001, max_value=5, max_digits=7, decimal_places=4)


class CoverDesignForm(forms.Form):
    """Local full-wrap simulator; artwork never leaves the Gaiden host."""

    LAYOUTS = (
        ("classic", "Clássico"),
        ("modern", "Moderno"),
        ("minimal", "Minimalista"),
        ("art_only", "Arte pronta (sem texto sobre a imagem)"),
    )
    IMAGE_FITS = (
        ("cover", "Preencher e recortar"),
        ("contain", "Mostrar imagem inteira"),
    )
    ENHANCEMENT_PROFILES = (
        ("none", "Original, sem correção"),
        ("print_safe", "Impressão segura: contraste e nitidez suaves"),
        ("detail", "Detalhes: redução de ruído e nitidez reforçada"),
        ("production_300", "Produção 300 ppi: ampliar com Lanczos e finalizar nitidez"),
    )
    PALETTES = (
        ("midnight", "Meia-noite editorial"),
        ("ivory", "Marfim clássico"),
        ("burgundy", "Bordô literário"),
        ("forest", "Verde biblioteca"),
        ("custom", "Cores personalizadas"),
    )
    save_mode = forms.ChoiceField(
        choices=(("draft", "Rascunho"), ("final", "Final")),
        initial="draft",
        widget=forms.HiddenInput,
    )

    cover_image = forms.FileField(
        label="1. Capa pronta em imagem (PNG/JPEG)",
        required=False,
        help_text="Fluxo padrão: envie a arte externa pronta. Ela aparece imediatamente no simulador.",
    )
    add_front_text = forms.BooleanField(
        label="Preciso adicionar textos sobre a imagem da frente",
        required=False,
        initial=False,
    )
    layout = forms.ChoiceField(label="Modelo dos textos", choices=LAYOUTS[:3], initial="classic", required=False)
    enhancement_profile = forms.ChoiceField(
        label="Melhoria OpenCV",
        choices=ENHANCEMENT_PROFILES,
        initial="print_safe",
        help_text="Processamento local. O perfil 300 ppi amplia com Lanczos e registra o fator aplicado.",
    )
    title = forms.CharField(label="Título da capa", max_length=180, required=False)
    subtitle = forms.CharField(label="Subtítulo", max_length=240, required=False)
    author = forms.CharField(label="Autor", max_length=180, required=False)
    back_heading = forms.CharField(label="Título da contracapa", max_length=180, required=False)
    back_text = forms.CharField(label="Sinopse", max_length=2400, required=False, widget=forms.Textarea(attrs={"rows": 7}))
    back_quote = forms.CharField(label="Citação ou destaque", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    back_bio = forms.CharField(label="Sobre o autor", max_length=900, required=False, widget=forms.Textarea(attrs={"rows": 4}))
    spine_text = forms.CharField(label="Texto da lombada", max_length=180, required=False)
    image_fit = forms.ChoiceField(label="Enquadramento", choices=IMAGE_FITS, initial="cover")
    image_zoom = forms.IntegerField(
        label="Zoom da imagem (%)",
        min_value=100,
        max_value=180,
        initial=100,
        widget=forms.NumberInput(attrs={"type": "range", "min": 100, "max": 180, "step": 1}),
    )
    image_x = forms.IntegerField(
        label="Posição horizontal (%)",
        min_value=0,
        max_value=100,
        initial=50,
        widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 100, "step": 1}),
    )
    image_y = forms.IntegerField(
        label="Posição vertical (%)",
        min_value=0,
        max_value=100,
        initial=50,
        widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 100, "step": 1}),
    )
    overlay_opacity = forms.IntegerField(
        label="Escurecimento para leitura (%)",
        min_value=0,
        max_value=80,
        initial=0,
        widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 80, "step": 1}),
    )
    background_color = forms.RegexField(label="Cor de fundo (#RRGGBB)", regex=r"^#?[0-9a-fA-F]{6}$", initial="#1F2937")
    text_color = forms.RegexField(label="Cor do texto (#RRGGBB)", regex=r"^#?[0-9a-fA-F]{6}$", initial="#FFFFFF")
    accent_color = forms.RegexField(label="Cor de destaque (#RRGGBB)", regex=r"^#?[0-9a-fA-F]{6}$", initial="#D4A24C")
    back_palette = forms.ChoiceField(label="Paleta da contracapa", choices=PALETTES, initial="midnight")
    back_background_color = forms.RegexField(label="Fundo da contracapa", regex=r"^#?[0-9a-fA-F]{6}$", initial="#1F2937")
    back_text_color = forms.RegexField(label="Texto da contracapa", regex=r"^#?[0-9a-fA-F]{6}$", initial="#FFFFFF")
    back_accent_color = forms.RegexField(label="Destaque da contracapa", regex=r"^#?[0-9a-fA-F]{6}$", initial="#D4A24C")
    spine = forms.DecimalField(
        label="Largura da lombada (polegadas)",
        min_value=0.001,
        max_value=5,
        max_digits=7,
        decimal_places=4,
        required=False,
        help_text="O simulador sugere uma estimativa. Substitua pelo valor do template oficial para produção.",
    )
    template = forms.FileField(
        label="Template oficial da gráfica (PDF, opcional na prova)",
        required=False,
        help_text="Sem este PDF a capa é salva como PROVA e não pode seguir para o preflight.",
    )

    def clean_template(self):
        upload = self.cleaned_data.get("template")
        if upload is None:
            return None
        if upload.size > 100 * 1024 * 1024:
            raise forms.ValidationError("O template deve ter no máximo 100 MiB.")
        if not upload.name.lower().endswith(".pdf"):
            raise forms.ValidationError(
                "Este campo aceita somente o PDF do template. Use o primeiro campo para a imagem da capa."
            )
        return upload

    def clean_cover_image(self):
        upload = self.cleaned_data.get("cover_image")
        if upload is None:
            return None
        if upload.size > 50 * 1024 * 1024:
            raise forms.ValidationError("A imagem da frente deve ter no máximo 50 MiB.")
        if upload.name.lower().rsplit(".", 1)[-1] not in {"png", "jpg", "jpeg"}:
            raise forms.ValidationError("Use uma imagem PNG ou JPEG.")
        try:
            from PIL import Image, UnidentifiedImageError

            upload.seek(0)
            with Image.open(upload) as image:
                image.verify()
            upload.seek(0)
        except (OSError, UnidentifiedImageError) as exc:
            raise forms.ValidationError("A imagem da frente não pôde ser lida.") from exc
        return upload

    def clean(self):
        data = super().clean()
        if data.get("add_front_text"):
            if not (data.get("title") or "").strip():
                self.add_error("title", "Informe o título quando os textos da frente estiverem ativados.")
            if not (data.get("author") or "").strip():
                self.add_error("author", "Informe o autor quando os textos da frente estiverem ativados.")
            data["layout"] = data.get("layout") or "classic"
        else:
            data["layout"] = "art_only"
            data["overlay_opacity"] = 0
        return data
