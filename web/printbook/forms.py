from decimal import Decimal

from django import forms
from editorial.models import EditionBuild
from gaiden.print_engine.isbn import isbn13
from .models import PrintEdition, TypographyProfile
from .services import spec_for
from .catalog import catalog, eligible
from .trim_sizes import compatibility_map


CANONICAL_TYPEFACE_NAMES = (
    "TeX Gyre Pagella",
    "TeX Gyre Termes",
    "TeX Gyre Schola",
    "TeX Gyre Bonum",
    "Noto Serif",
)


def configure_typeface_field(form):
    field = form.fields["typography"]
    field.label = "Fonte do miolo"
    field.help_text = "Fontes instaladas e validadas para composição com LuaLaTeX."
    field.empty_label = None
    field.queryset = TypographyProfile.objects.filter(name__in=CANONICAL_TYPEFACE_NAMES).order_by("pk")

    if not form.is_bound and not form.initial.get("typography") and not getattr(form.instance, "pk", None):
        default_profile = field.queryset.filter(name="TeX Gyre Pagella").first()
        if default_profile:
            form.initial["typography"] = default_profile.pk

    current = getattr(getattr(form, "instance", None), "typography_id", None)
    if current and not field.queryset.filter(pk=current).exists():
        current_profile = TypographyProfile.objects.filter(pk=current).first()
        replacement = field.queryset.filter(font=current_profile.font).first() if current_profile else None
        if replacement:
            form.initial["typography"] = replacement.pk


class EditionForm(forms.ModelForm):
    class Meta:
        model = PrintEdition
        fields = ["source", "name", "platform", "binding", "trim", "paper", "typography", "cover_mode", "isbn", "barcode_mode"]
        labels = {
            "source": "Build editorial final aprovado",
            "name": "Nome da edição física",
            "platform": "Fornecedor",
            "binding": "Encadernação",
            "trim": "Formato",
            "paper": "Papel",
            "typography": "Fonte do miolo",
            "cover_mode": "Capa da edição impressa",
            "isbn": "ISBN exclusivo desta edição física",
            "barcode_mode": "Código de barras",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].queryset = EditionBuild.objects.filter(pk__in=[source.pk for source in catalog()])
        # Backward-compatible for existing creation clients that predate the
        # provider/binding selectors; the visible form still supplies both.
        self.fields["platform"].required = False
        self.fields["binding"].required = False
        self.fields["cover_mode"].help_text = "A capa é independente do miolo e poderá ser substituída na etapa de confecção."
        self.trim_compatibility = compatibility_map()
        configure_typeface_field(self)

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
        data["platform"] = data.get("platform") or "ingramspark"
        data["binding"] = data.get("binding") or "paperback"
        if all(data.get(key) for key in ("platform", "binding", "trim", "paper", "typography")):
            candidate = PrintEdition(
                platform=data["platform"], binding=data["binding"], trim=data["trim"],
                paper=data["paper"], typography=data["typography"],
            )
            try:
                spec_for(candidate)
            except ValueError as exc:
                raise forms.ValidationError(str(exc)) from exc
        return data


class EditionConfigurationForm(EditionForm):
    """Editable print settings; the approved editorial source is immutable."""

    class Meta(EditionForm.Meta):
        fields = ["name", "platform", "binding", "trim", "paper", "typography", "cover_mode", "isbn", "barcode_mode"]

    def __init__(self, *args, **kwargs):
        forms.ModelForm.__init__(self, *args, **kwargs)
        self.trim_compatibility = compatibility_map()
        configure_typeface_field(self)

    def clean(self):
        data = forms.ModelForm.clean(self)
        if all(data.get(key) for key in ("platform", "binding", "trim", "paper", "typography")):
            candidate = PrintEdition(
                platform=data["platform"],
                binding=data["binding"],
                trim=data["trim"],
                paper=data["paper"],
                typography=data["typography"],
            )
            try:
                spec_for(candidate)
            except ValueError as exc:
                raise forms.ValidationError(str(exc)) from exc
        return data


class InteriorSettingsForm(forms.Form):
    FONT_CHOICES = (
        ("TeX Gyre Pagella", "TeX Gyre Pagella"),
        ("TeX Gyre Termes", "TeX Gyre Termes"),
        ("Libertinus Serif", "Libertinus Serif"),
        ("Noto Serif", "Noto Serif"),
    )
    HEADER_CHOICES = (("none", "Sem cabeçalho"), ("title", "Título do livro"), ("chapter", "Capítulo atual"))
    FOOTER_CHOICES = (("page", "Número da página"), ("none", "Sem rodapé"))
    CHAPTER_CHOICES = (("recto", "Nova página ímpar"), ("new_page", "Nova página"), ("inline", "Na sequência"))

    body_font = forms.ChoiceField(label="Fonte do miolo", choices=FONT_CHOICES)
    font_size_pt = forms.DecimalField(label="Tamanho da fonte (pt)", min_value=8, max_value=18, decimal_places=2)
    leading_pt = forms.DecimalField(label="Entrelinha (pt)", min_value=9, max_value=24, decimal_places=2)
    paragraph_indent_em = forms.DecimalField(label="Recuo do parágrafo (em)", min_value=0, max_value=4, decimal_places=2)
    inner_margin_in = forms.DecimalField(label="Margem interna (in)", min_value=Decimal("0.25"), max_value=2, decimal_places=3)
    outer_margin_in = forms.DecimalField(label="Margem externa (in)", min_value=Decimal("0.25"), max_value=2, decimal_places=3)
    top_margin_in = forms.DecimalField(label="Margem superior (in)", min_value=Decimal("0.25"), max_value=2, decimal_places=3)
    bottom_margin_in = forms.DecimalField(label="Margem inferior (in)", min_value=Decimal("0.25"), max_value=2, decimal_places=3)
    header_style = forms.ChoiceField(label="Cabeçalhos", choices=HEADER_CHOICES)
    footer_style = forms.ChoiceField(label="Rodapés", choices=FOOTER_CHOICES)
    page_numbering = forms.BooleanField(label="Exibir numeração", required=False)
    chapter_opening = forms.ChoiceField(label="Abertura de capítulos", choices=CHAPTER_CHOICES)

    def clean(self):
        data = super().clean()
        size, leading = data.get("font_size_pt"), data.get("leading_pt")
        if size is not None and leading is not None and leading < size:
            self.add_error("leading_pt", "A entrelinha não pode ser menor que a fonte.")
        return data


class CoverForm(forms.Form):
    cover = forms.FileField(label="PDF da capa completa")
    template = forms.FileField(label="PDF do template oficial para este miolo")
    spine = forms.DecimalField(label="Lombada indicada no template (polegadas)", min_value=Decimal("0.001"), max_value=5, max_digits=7, decimal_places=4)


class CoverCompositionForm(forms.Form):
    front = forms.FileField(label="Frente em PDF (uma página, com sangria externa)")
    back = forms.FileField(label="Contracapa em PDF (uma página, com sangria externa)")
    template = forms.FileField(label="Template oficial para a paginação final")
    spine = forms.DecimalField(label="Lombada do template (polegadas)", min_value=Decimal("0.001"), max_value=5, max_digits=7, decimal_places=4)


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
    COVER_FONTS = (
        ("EB Garamond", "EB Garamond — clássico editorial e literatura"),
        ("GFS Didot", "GFS Didot — luxo, arte e alto contraste"),
        ("GFS BodoniClassic", "GFS Bodoni — ensaio, moda e edição premium"),
        ("GFS Baskerville", "GFS Baskerville — tradição e autoridade"),
        ("Berenis ADF Pro", "Berenis ADF Pro — literário e elegante"),
        ("Accanthis ADF Std", "Accanthis ADF — humanista e clássico"),
        ("Gentium Book Plus", "Gentium Book Plus — leitura e humanidades"),
        ("Charis SIL", "Charis SIL — acadêmico e histórico"),
        ("Noto Serif Display", "Noto Serif Display — contemporâneo editorial"),
        ("Fira Sans", "Fira Sans — não ficção contemporânea"),
    )
    ENHANCEMENT_PROFILES = (
        ("none", "Original, sem correção"),
        ("print_safe", "Impressão segura: contraste e nitidez suaves"),
        ("detail", "Detalhes: redução de ruído e nitidez reforçada"),
        ("production_300", "Produção 300 ppi: ampliar com Lanczos e finalizar nitidez"),
    )
    FRONT_PALETTES = (
        ("classic_blue", "Azul clássico"),
        ("classic_red", "Vermelho clássico"),
        ("classic_gray", "Cinza clássico"),
        ("classic_black", "Preto clássico"),
        ("classic_green", "Verde clássico"),
        ("custom", "Cor personalizada"),
    )
    BACK_PALETTES = (
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
        label="Selecionar imagem de fundo da capa (PNG/JPEG)",
        required=False,
        help_text="Clique para escolher a imagem. Ela aparece imediatamente atrás dos textos flutuantes.",
    )
    back_image = forms.FileField(
        label="Imagem da contracapa (PNG/JPEG)", required=False,
        help_text="Opcional. A imagem original é preservada e o recorte só acontece na renderização.",
    )
    spine_image = forms.FileField(
        label="Imagem de fundo da lombada (PNG/JPEG)", required=False,
        help_text="Opcional. A imagem é recortada dentro da largura calculada da lombada.",
    )
    add_front_text = forms.BooleanField(
        label="Usar título, subtítulo, autor, língua e editora sobre o fundo da frente",
        required=False,
        initial=True,
    )
    layout = forms.ChoiceField(label="Diagramação da capa", choices=LAYOUTS[:3], initial="classic", required=False)
    enhancement_profile = forms.ChoiceField(
        label="Melhoria OpenCV",
        choices=ENHANCEMENT_PROFILES,
        initial="print_safe",
        help_text="Processamento local. O perfil 300 ppi amplia com Lanczos e registra o fator aplicado.",
    )
    title = forms.CharField(label="Título da capa", max_length=180, required=False)
    subtitle = forms.CharField(label="Subtítulo", max_length=240, required=False)
    author = forms.CharField(label="Autor", max_length=180, required=False)
    language = forms.CharField(label="Língua", max_length=80, required=False)
    publisher = forms.CharField(label="Editora (pé da capa)", max_length=120, required=False)
    front_title_y_percent = forms.IntegerField(
        label="Posição vertical do título (%)", min_value=8, max_value=70, initial=20,
        help_text="Distância a partir do topo da frente.", required=False,
    )
    front_title_x_percent = forms.IntegerField(label="Posição horizontal do título (%)", min_value=10, max_value=90, initial=50, required=False)
    front_subtitle_x_percent = forms.IntegerField(label="Posição horizontal do subtítulo (%)", min_value=10, max_value=90, initial=50, required=False)
    front_subtitle_y_percent = forms.IntegerField(label="Posição vertical do subtítulo (%)", min_value=10, max_value=82, initial=34, required=False)
    front_author_y_percent = forms.IntegerField(
        label="Posição vertical do autor (%)", min_value=45, max_value=92, initial=84,
        help_text="Distância a partir do topo da frente.", required=False,
    )
    front_author_x_percent = forms.IntegerField(label="Posição horizontal do autor (%)", min_value=10, max_value=90, initial=50, required=False)
    front_language_x_percent = forms.IntegerField(label="Posição horizontal da língua (%)", min_value=10, max_value=90, initial=50, required=False)
    front_language_y_percent = forms.IntegerField(label="Posição vertical da língua (%)", min_value=70, max_value=94, initial=84, required=False)
    front_text_scale_percent = forms.IntegerField(
        label="Escala dos textos (%)", min_value=50, max_value=160, initial=100,
        required=False,
    )
    front_text_align = forms.ChoiceField(
        label="Alinhamento dos textos",
        choices=(("left", "Esquerda"), ("center", "Centro"), ("right", "Direita")),
        initial="center", required=False,
    )
    front_publisher_y_percent = forms.IntegerField(
        label="Posição da editora (%)", min_value=78, max_value=96, initial=89,
        help_text="A editora permanece no pé da capa, dentro da área segura.", required=False,
    )
    front_publisher_x_percent = forms.IntegerField(label="Posição horizontal da editora (%)", min_value=10, max_value=90, initial=50, required=False)
    front_palette = forms.ChoiceField(
        label="Cor clássica de fundo", choices=FRONT_PALETTES, initial="classic_blue", required=False,
        help_text="A imagem enviada permanece em segundo plano; a cor funciona como base da frente.",
    )
    back_heading = forms.CharField(label="Título da contracapa", max_length=180, required=False)
    back_text = forms.CharField(label="Sobre o livro", max_length=2400, required=False, widget=forms.Textarea(attrs={"rows": 7}))
    approve_empty_back = forms.BooleanField(
        label="Aprovar contracapa sem texto", required=False,
        help_text="Marque somente quando a contracapa deve usar apenas cor ou imagem de fundo.",
    )
    back_text_x_percent = forms.IntegerField(label="Posição horizontal do bloco (%)", min_value=10, max_value=90, initial=50, required=False)
    back_text_y_percent = forms.IntegerField(label="Posição vertical do bloco (%)", min_value=8, max_value=75, initial=12, required=False)
    back_quote = forms.CharField(label="Citação ou destaque", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    back_bio = forms.CharField(label="Sobre o autor", max_length=900, required=False, widget=forms.Textarea(attrs={"rows": 4}))
    back_footer = forms.CharField(
        label="Rodapé editorial / créditos", max_length=500, required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Coleção, editora, site ou outros créditos que devem aparecer na base da contracapa.",
    )
    spine_text = forms.CharField(label="Texto legado da lombada", max_length=180, required=False, widget=forms.HiddenInput)
    spine_title = forms.CharField(label="Título", max_length=180, required=False)
    spine_author = forms.CharField(label="Autor", max_length=180, required=False)
    spine_publisher = forms.CharField(label="Editora / selo", max_length=120, required=False)
    cover_font_family = forms.ChoiceField(
        label="Família tipográfica da capa", choices=COVER_FONTS,
        initial="EB Garamond", required=False,
        help_text="Fontes instaladas e utilizáveis pelo LuaLaTeX.",
    )
    front_title_font_family = forms.ChoiceField(label="Fonte do título", choices=COVER_FONTS, initial="EB Garamond", required=False)
    front_subtitle_font_family = forms.ChoiceField(label="Fonte do subtítulo", choices=COVER_FONTS, initial="EB Garamond", required=False)
    front_author_font_family = forms.ChoiceField(label="Fonte do autor", choices=COVER_FONTS, initial="EB Garamond", required=False)
    front_language_font_family = forms.ChoiceField(label="Fonte da língua", choices=COVER_FONTS, initial="EB Garamond", required=False)
    front_publisher_font_family = forms.ChoiceField(label="Fonte da editora", choices=COVER_FONTS, initial="EB Garamond", required=False)
    front_title_color = forms.RegexField(label="Cor do título", regex=r"^#?[0-9a-fA-F]{6}$", initial="#FFFFFF", required=False, widget=forms.TextInput(attrs={"type": "color"}))
    front_subtitle_color = forms.RegexField(label="Cor do subtítulo", regex=r"^#?[0-9a-fA-F]{6}$", initial="#D4A24C", required=False, widget=forms.TextInput(attrs={"type": "color"}))
    front_author_color = forms.RegexField(label="Cor do autor", regex=r"^#?[0-9a-fA-F]{6}$", initial="#D4A24C", required=False, widget=forms.TextInput(attrs={"type": "color"}))
    front_language_color = forms.RegexField(label="Cor da língua", regex=r"^#?[0-9a-fA-F]{6}$", initial="#D4A24C", required=False, widget=forms.TextInput(attrs={"type": "color"}))
    front_publisher_color = forms.RegexField(label="Cor da editora", regex=r"^#?[0-9a-fA-F]{6}$", initial="#D4A24C", required=False, widget=forms.TextInput(attrs={"type": "color"}))
    front_title_font_size_pt = forms.DecimalField(label="Título (pt)", min_value=12, max_value=72, decimal_places=1, initial=28, required=False)
    front_subtitle_font_size_pt = forms.DecimalField(label="Subtítulo (pt)", min_value=8, max_value=42, decimal_places=1, initial=14, required=False)
    front_author_font_size_pt = forms.DecimalField(label="Autor (pt)", min_value=8, max_value=36, decimal_places=1, initial=12, required=False)
    front_language_font_size_pt = forms.DecimalField(label="Língua (pt)", min_value=6, max_value=24, decimal_places=1, initial=8, required=False)
    front_publisher_font_size_pt = forms.DecimalField(label="Editora (pt)", min_value=6, max_value=24, decimal_places=1, initial=10, required=False)
    front_title_weight = forms.ChoiceField(label="Peso do título", choices=(("regular", "Regular"), ("bold", "Negrito")), initial="bold", required=False)
    FONT_SHAPES = (("normal", "Normal"), ("italic", "Itálico"), ("small_caps", "Versalete"))
    FONT_WEIGHTS = (("regular", "Regular"), ("bold", "Negrito"))
    front_title_style = forms.ChoiceField(label="Forma do título", choices=FONT_SHAPES, initial="normal", required=False)
    front_subtitle_weight = forms.ChoiceField(label="Peso do subtítulo", choices=FONT_WEIGHTS, initial="regular", required=False)
    front_subtitle_style = forms.ChoiceField(label="Forma do subtítulo", choices=FONT_SHAPES, initial="normal", required=False)
    front_author_weight = forms.ChoiceField(label="Peso do autor", choices=FONT_WEIGHTS, initial="regular", required=False)
    front_author_style = forms.ChoiceField(label="Forma do autor", choices=FONT_SHAPES, initial="normal", required=False)
    front_language_weight = forms.ChoiceField(label="Peso da língua", choices=FONT_WEIGHTS, initial="regular", required=False)
    front_language_style = forms.ChoiceField(label="Forma da língua", choices=FONT_SHAPES, initial="normal", required=False)
    front_publisher_weight = forms.ChoiceField(label="Peso da editora", choices=FONT_WEIGHTS, initial="regular", required=False)
    front_publisher_style = forms.ChoiceField(label="Forma da editora", choices=FONT_SHAPES, initial="normal", required=False)
    front_title_letter_spacing_pt = forms.DecimalField(label="Espaçamento do título (pt)", min_value=-1, max_value=8, decimal_places=2, initial=0, required=False)
    front_subtitle_letter_spacing_pt = forms.DecimalField(label="Espaçamento do subtítulo (pt)", min_value=-1, max_value=8, decimal_places=2, initial=0, required=False)
    front_author_letter_spacing_pt = forms.DecimalField(label="Espaçamento do autor (pt)", min_value=-1, max_value=8, decimal_places=2, initial=0, required=False)
    front_language_letter_spacing_pt = forms.DecimalField(label="Espaçamento da língua (pt)", min_value=-1, max_value=8, decimal_places=2, initial=0, required=False)
    front_publisher_letter_spacing_pt = forms.DecimalField(label="Espaçamento da editora (pt)", min_value=-1, max_value=8, decimal_places=2, initial=0, required=False)
    back_heading_font_size_pt = forms.DecimalField(label="Título da contracapa (pt)", min_value=8, max_value=36, decimal_places=1, initial=17, required=False)
    back_body_font_size_pt = forms.DecimalField(label="Texto sobre o livro (pt)", min_value=7, max_value=18, decimal_places=1, initial=10.5, required=False)
    back_text_align = forms.ChoiceField(label="Alinhamento da contracapa", choices=(("left", "Esquerda"), ("justify", "Justificado"), ("center", "Centro")), initial="left", required=False)
    spine_font_family = forms.ChoiceField(label="Família tipográfica", choices=COVER_FONTS, initial="EB Garamond", required=False)
    spine_font_size_pt = forms.DecimalField(label="Tamanho (pt)", min_value=5, max_value=36, decimal_places=1, initial=8, required=False)
    spine_font_weight = forms.ChoiceField(label="Peso", choices=(("regular", "Regular"), ("bold", "Negrito")), initial="regular", required=False)
    spine_color = forms.RegexField(label="Cor", regex=r"^#?[0-9a-fA-F]{6}$", initial="#FFFFFF", required=False)
    spine_letter_spacing_pt = forms.DecimalField(label="Espaçamento (pt)", min_value=-1, max_value=8, decimal_places=2, initial=0, required=False)
    spine_align_horizontal = forms.ChoiceField(label="Alinhamento horizontal", choices=(("left", "Esquerda"), ("center", "Centro"), ("right", "Direita")), initial="center", required=False)
    spine_align_vertical = forms.ChoiceField(label="Alinhamento vertical", choices=(("top", "Topo"), ("center", "Centro"), ("bottom", "Base")), initial="center", required=False)
    spine_offset_x_mm = forms.DecimalField(label="Deslocamento horizontal (mm)", min_value=-50, max_value=50, decimal_places=1, initial=0, required=False)
    spine_offset_y_mm = forms.DecimalField(label="Deslocamento vertical (mm)", min_value=-100, max_value=100, decimal_places=1, initial=0, required=False)
    spine_text_x_percent = forms.IntegerField(label="Posição horizontal do texto (%)", min_value=10, max_value=90, initial=50, required=False)
    spine_text_y_percent = forms.IntegerField(label="Posição vertical do texto (%)", min_value=10, max_value=90, initial=50, required=False)
    spine_rotation = forms.ChoiceField(label="Sentido de leitura", choices=(("90", "De baixo para cima"), ("-90", "De cima para baixo")), initial="90", required=False)
    spine_edge_safe_mm = forms.DecimalField(
        label="Distância das dobras (mm)", min_value=Decimal("1.6"), max_value=20,
        decimal_places=1, initial=Decimal("1.6"), required=False,
    )
    image_fit = forms.ChoiceField(label="Enquadramento", choices=IMAGE_FITS, initial="cover")
    image_zoom = forms.IntegerField(
        label="Zoom da imagem (%)",
        min_value=25,
        max_value=400,
        initial=100,
        widget=forms.NumberInput(attrs={"type": "range", "min": 25, "max": 400, "step": 1}),
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
    image_offset_x_mm = forms.DecimalField(label="Deslocamento X (mm)", min_value=-300, max_value=300, decimal_places=1, initial=0, required=False)
    image_offset_y_mm = forms.DecimalField(label="Deslocamento Y (mm)", min_value=-300, max_value=300, decimal_places=1, initial=0, required=False)
    image_rotation_deg = forms.DecimalField(label="Rotação (°)", min_value=-180, max_value=180, decimal_places=1, initial=0, required=False)
    back_image_fit = forms.ChoiceField(label="Enquadramento da contracapa", choices=IMAGE_FITS, initial="cover", required=False)
    back_image_zoom = forms.IntegerField(label="Escala da contracapa (%)", min_value=25, max_value=400, initial=100, required=False)
    back_image_x = forms.IntegerField(label="Ponto focal X (%)", min_value=0, max_value=100, initial=50, required=False)
    back_image_y = forms.IntegerField(label="Ponto focal Y (%)", min_value=0, max_value=100, initial=50, required=False)
    back_image_offset_x_mm = forms.DecimalField(label="Deslocamento X da contracapa (mm)", min_value=-300, max_value=300, decimal_places=1, initial=0, required=False)
    back_image_offset_y_mm = forms.DecimalField(label="Deslocamento Y da contracapa (mm)", min_value=-300, max_value=300, decimal_places=1, initial=0, required=False)
    back_image_rotation_deg = forms.DecimalField(label="Rotação da contracapa (°)", min_value=-180, max_value=180, decimal_places=1, initial=0, required=False)
    spine_image_fit = forms.ChoiceField(label="Enquadramento da lombada", choices=IMAGE_FITS, initial="cover", required=False)
    spine_image_zoom = forms.IntegerField(label="Escala da imagem da lombada (%)", min_value=25, max_value=400, initial=100, required=False)
    spine_image_x = forms.IntegerField(label="Ponto focal X da lombada (%)", min_value=0, max_value=100, initial=50, required=False)
    spine_image_y = forms.IntegerField(label="Ponto focal Y da lombada (%)", min_value=0, max_value=100, initial=50, required=False)
    allow_empty_areas = forms.BooleanField(label="Permitir áreas vazias intencionais", required=False, initial=False)
    minimum_dpi = forms.IntegerField(label="DPI efetivo mínimo", min_value=72, max_value=1200, initial=300, required=False)
    overlay_opacity = forms.IntegerField(
        label="Escurecimento para leitura (%)",
        min_value=0,
        max_value=80,
        initial=0,
        widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 80, "step": 1}),
    )
    background_color = forms.RegexField(label="Cor de fundo (#RRGGBB)", regex=r"^#?[0-9a-fA-F]{6}$", initial="#17365D")
    text_color = forms.RegexField(label="Cor do texto (#RRGGBB)", regex=r"^#?[0-9a-fA-F]{6}$", initial="#FFFFFF")
    accent_color = forms.RegexField(label="Cor de destaque (#RRGGBB)", regex=r"^#?[0-9a-fA-F]{6}$", initial="#D4A24C")
    back_palette = forms.ChoiceField(label="Paleta da contracapa", choices=BACK_PALETTES, initial="midnight")
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
        return self._clean_raster("cover_image", "frente")

    def clean_back_image(self):
        return self._clean_raster("back_image", "contracapa")

    def clean_spine_image(self):
        return self._clean_raster("spine_image", "lombada")

    def _clean_raster(self, field_name, label):
        upload = self.cleaned_data.get(field_name)
        if upload is None:
            return None
        if upload.size > 50 * 1024 * 1024:
            raise forms.ValidationError(f"A imagem da {label} deve ter no máximo 50 MiB.")
        if upload.name.lower().rsplit(".", 1)[-1] not in {"png", "jpg", "jpeg"}:
            raise forms.ValidationError("Use uma imagem PNG ou JPEG.")
        try:
            from PIL import Image, UnidentifiedImageError

            upload.seek(0)
            with Image.open(upload) as image:
                image.verify()
            upload.seek(0)
        except (OSError, UnidentifiedImageError) as exc:
            raise forms.ValidationError(f"A imagem da {label} não pôde ser lida.") from exc
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
        defaults = {
            "image_offset_x_mm": 0, "image_offset_y_mm": 0, "image_rotation_deg": 0,
            "back_image_fit": "cover", "back_image_zoom": 100, "back_image_x": 50, "back_image_y": 50,
            "back_image_offset_x_mm": 0, "back_image_offset_y_mm": 0, "back_image_rotation_deg": 0,
            "front_title_y_percent": 20, "front_author_y_percent": 84,
            "front_title_x_percent": 50, "front_subtitle_x_percent": 50,
            "front_subtitle_y_percent": 34, "front_author_x_percent": 50,
            "front_language_x_percent": 50, "front_language_y_percent": 84,
            "front_text_scale_percent": 100, "front_text_align": "center",
            "front_palette": "classic_blue",
            "front_publisher_y_percent": 89, "cover_font_family": "EB Garamond",
            "front_title_font_family": "EB Garamond", "front_subtitle_font_family": "EB Garamond",
            "front_author_font_family": "EB Garamond", "front_language_font_family": "EB Garamond",
            "front_publisher_font_family": "EB Garamond",
            "front_title_color": "#FFFFFF", "front_subtitle_color": "#D4A24C",
            "front_author_color": "#D4A24C", "front_language_color": "#D4A24C",
            "front_publisher_color": "#D4A24C",
            "front_publisher_x_percent": 50,
            "front_title_font_size_pt": 28, "front_subtitle_font_size_pt": 14,
            "front_author_font_size_pt": 12, "front_language_font_size_pt": 8,
            "front_publisher_font_size_pt": 10,
            "front_title_weight": "bold", "back_heading_font_size_pt": 17,
            "front_title_style": "normal", "front_subtitle_weight": "regular",
            "front_subtitle_style": "normal", "front_author_weight": "regular",
            "front_author_style": "normal", "front_language_weight": "regular",
            "front_language_style": "normal", "front_publisher_weight": "regular",
            "front_publisher_style": "normal", "front_title_letter_spacing_pt": 0,
            "front_subtitle_letter_spacing_pt": 0, "front_author_letter_spacing_pt": 0,
            "front_language_letter_spacing_pt": 0,
            "front_publisher_letter_spacing_pt": 0,
            "back_body_font_size_pt": 10.5, "back_text_align": "left",
            "back_text_x_percent": 50, "back_text_y_percent": 12,
            "spine_image_fit": "cover", "spine_image_zoom": 100,
            "spine_image_x": 50, "spine_image_y": 50,
            "minimum_dpi": 300, "spine_font_family": "EB Garamond", "spine_font_size_pt": 8,
            "spine_font_weight": "regular", "spine_color": "#FFFFFF", "spine_letter_spacing_pt": 0,
            "spine_align_horizontal": "center", "spine_align_vertical": "center",
            "spine_offset_x_mm": 0, "spine_offset_y_mm": 0, "spine_rotation": "90", "spine_edge_safe_mm": 1.6,
            "spine_text_x_percent": 50, "spine_text_y_percent": 50,
        }
        for key, default in defaults.items():
            if data.get(key) in (None, ""):
                data[key] = default
        data["spine_title"] = (data.get("spine_title") or data.get("spine_text") or "").strip()
        if data.get("save_mode") == "final":
            if not data.get("approve_empty_back") and not (data.get("back_heading") or "").strip():
                self.add_error("back_heading", "Informe a chamada da contracapa antes de salvar a capa final.")
            if not data.get("approve_empty_back") and not (data.get("back_text") or "").strip():
                self.add_error("back_text", "Informe o texto sobre o livro ou aprove a contracapa sem texto.")
        return data
