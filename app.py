"""
Erhebungsbogen Assistent – Streamlit Web-App
============================================
Deployment: https://streamlit.io/cloud
Lokal testen: streamlit run app.py
"""

import io, re, json
from datetime import datetime
import streamlit as st
import pdfplumber
import pypdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.pdfgen import canvas as rl_canvas

# ════════════════════════════════════════════════════════════════════════════
# Seiten-Konfiguration
# ════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Erhebungsbogen Assistent",
    page_icon="📋",
    layout="centered",
)

st.markdown("""
<style>
  .stApp { max-width: 860px; margin: 0 auto; }
  .block-container { padding-top: 2rem; }
  div[data-testid="stFileUploadDropzone"] { border: 2px dashed #93c5fd; border-radius: 10px; }
  .badge { background: #ebf5ff; color: #1e3a8a; padding: 6px 14px;
           border-radius: 20px; font-size: 13px; font-weight: 600;
           border-left: 4px solid #1a56db; display: inline-block; margin: 8px 0; }
  .missing { background: #fff7ed; color: #92400e; padding: 10px 14px;
             border-radius: 8px; border-left: 4px solid #f59e0b; font-size: 13px; }
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen (identisch mit Tornado-Version)
# ════════════════════════════════════════════════════════════════════════════

def extract_text_pdf(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)

def extract_text_docx(data: bytes) -> str:
    try:
        from docx import Document
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception:
        return ""

def extract_cv(file_data: bytes, filename: str) -> dict:
    text = ""
    if filename.lower().endswith(".pdf"):
        text = extract_text_pdf(file_data)
    elif filename.lower().endswith((".docx", ".doc")):
        text = extract_text_docx(file_data)

    d = {}
    name_re = re.compile(r'^([A-ZÜÄÖ][a-züäöß]+(?:\s+(?:von|van|de|der|le)?\s*[A-ZÜÄÖ][a-züäöß\-]+){1,3})$')
    for line in text.split("\n")[:12]:
        m = name_re.match(line.strip())
        if m:
            parts = m.group(1).split()
            d["vorname"]  = " ".join(parts[:-1])
            d["nachname"] = parts[-1]
            break

    m = re.search(r'(?:geboren|geburtsdatum|geb\.?)[:\s]*(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4})', text, re.I)
    if m: d["geburtsdatum"] = m.group(1).replace("-",".").replace("/",".")

    fs_m = re.search(r'familienstand[:\s]*([a-züäöß]+)', text, re.I)
    if fs_m: d["familienstand"] = fs_m.group(1).capitalize()

    sta_m = re.search(r'staatsangehörigkeit[:\s]*([a-züäöß]+)', text, re.I)
    if sta_m: d["staatsangehoerigkeit"] = sta_m.group(1).capitalize()

    if re.search(r'\bHerr\b', text):   d["geschlecht"] = "m"
    elif re.search(r'\bFrau\b', text): d["geschlecht"] = "w"

    addr_m = re.search(r'([A-ZÜÄÖ][a-züäöß\s\-]+(?:straße|str\.|gasse|weg|allee|platz|ring)\s+\d+[a-z]?)', text, re.I)
    if addr_m: d["strasse"] = addr_m.group(1).strip()

    plz_m = re.search(r'\b(\d{5})\s+([A-ZÜÄÖ][a-züäöß\-\s]+?)(?:\n|,|$)', text)
    if plz_m:
        d["plz"] = plz_m.group(1)
        d["ort"] = plz_m.group(2).strip()

    for m2 in re.finditer(r'(?:mobil|handy|tel\.?|telefon)[:\s]*([\+\d\s\-/()]{7,22})', text, re.I):
        num = m2.group(1).strip()
        label = m2.group(0).lower()
        if any(x in label for x in ["mobil","handy"]) or any(x in num for x in ["+49 1","017","015","016"]):
            d.setdefault("handy", num)
        else:
            d.setdefault("telefon", num)

    em = re.search(r'[\w.\-+]+@[\w.\-]+\.\w{2,}', text)
    if em: d["email"] = em.group(0)

    for abschluss, label in [
        ("Abitur","Abitur"), ("Fachabitur","Fachabitur"),
        ("Fachhochschulreife","Fachhochschulreife"),
        ("Bachelor","Hochschule/Universität"), ("Master","Hochschule/Universität"),
        ("Diplom","Hochschule/Universität"), ("Promotion","Hochschule/Universität"),
        ("Mittlere Reife","Realschulabschluss"), ("Realschulabschluss","Realschulabschluss"),
        ("Hauptschulabschluss","Hauptschulabschluss"),
    ]:
        if re.search(r'\b' + abschluss + r'\b', text, re.I):
            d["bildungsabschluss"] = label
            break

    ausb_m = re.search(r'(?:Ausbildung zum?r?|Berufsausbildung|Abschluss:)\s*([^\n]{5,60})', text, re.I)
    if ausb_m:
        d["berufsbezeichnung"] = ausb_m.group(1).strip().rstrip("–-")
        d["berufsabschluss"]   = "Ja"

    ag_m = re.search(r'(?:seit\s+[\d.]+\s*[–\-]\s*(?:heute|aktuell))?\s*\n([A-ZÜÄÖ][^\n]{3,60}(?:GmbH|AG|KG|OHG|UG|GbR|Inc|Ltd)[^\n]*)', text, re.I)
    if ag_m:
        d["firma"] = ag_m.group(1).strip()

    return d


def detect_form(file_data: bytes):
    reader = PdfReader(io.BytesIO(file_data))
    fields = reader.get_fields() or {}
    fillable = len(fields) > 0
    text = extract_text_pdf(file_data)
    combined = text + " " + " ".join(fields.keys())

    FORM_TYPES = {
        "ba_fw82":  ("BA-Fragebogen Beschäftigte (FW 82)",          ["BA I FW 82", "Sammelantragsverfahren"]),
        "ba_bq":    ("BA-Erhebungsbogen Beschäftigte (ba035330)",   ["txtfPersonVorname", "txtfBetrieb"]),
        "mannheim": ("Erhebungsbogen Mannheim BQ",                  ["Agentur für Arbeit Mannheim", "Erhebungsbogen Personen"]),
    }
    for ftype, (label, keywords) in FORM_TYPES.items():
        if all(kw.lower() in combined.lower() for kw in keywords):
            return ftype, label, fillable, fields

    return "generic", "Allgemeiner Erhebungsbogen", fillable, fields


def make_overlay(field_list, page_w, page_h, font_size=9):
    packet = io.BytesIO()
    c = rl_canvas.Canvas(packet, pagesize=(page_w, page_h))
    c.setFont("Helvetica", font_size)
    c.setFillColorRGB(0.04, 0.18, 0.58)
    for (x, y, text) in field_list:
        if text:
            c.drawString(float(x), float(y), str(text)[:120])
    c.save()
    packet.seek(0)
    return packet

def fill_fillable(file_data: bytes, pdf_fields: dict, field_map: dict) -> bytes:
    reader = PdfReader(io.BytesIO(file_data))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    for page in writer.pages:
        if "/Annots" not in page:
            continue
        for annot_ref in page["/Annots"]:
            try:
                annot = annot_ref.get_object()
                fname = str(annot.get("/T", ""))
                if fname in field_map:
                    val = field_map[fname]
                    if isinstance(val, bool):
                        v = "/On" if val else "/Off"
                        annot.update({NameObject("/V"): NameObject(v), NameObject("/AS"): NameObject(v)})
                    else:
                        annot.update({NameObject("/V"): pypdf.generic.create_string_object(str(val))})
            except Exception:
                pass
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

def fill_overlay(file_data: bytes, overlay_pages: dict) -> bytes:
    reader = PdfReader(io.BytesIO(file_data))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        writer.add_page(page)
        page_num = i + 1
        if page_num in overlay_pages:
            pw = float(page.mediabox.width)
            ph = float(page.mediabox.height)
            ov_data = make_overlay(overlay_pages[page_num], pw, ph)
            ov_reader = PdfReader(ov_data)
            writer.pages[i].merge_page(ov_reader.pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

def build_filled_pdf(file_data: bytes, form_type: str, pdf_fields: dict, f: dict) -> bytes:
    heute = datetime.today().strftime("%d.%m.%Y")

    if form_type == "ba_bq":
        field_map = {
            "txtfBetrieb": f.get("firma",""), "datePersonBeschaeftigt": f.get("eintritt",""),
            "txtfPersonVorname": f.get("vorname",""), "txtfPersonNachname": f.get("nachname",""),
            "txtPersonGebName": f.get("geburtsname",""), "datePersonGebDatum": f.get("geburtsdatum",""),
            "txtfPersonSVNr": f.get("svnr",""), "txtfPersonKundenNr": f.get("kundennr",""),
            "txtfPersonStrasse": f.get("strasse",""), "txtfPersonPLZ": f.get("plz",""),
            "txtfPersonOrt": f.get("ort",""), "txtfPersonTel": f.get("telefon",""),
            "txtfPersonEmail": f.get("email",""), "txtfBerufsbezeichnung": f.get("berufsbezeichnung",""),
            "dateZeugniss": f.get("datum_zeugnis",""),
            "txtfErklaerungOrt": f.get("ort",""), "txtfErklaerungDatum": heute,
        }
        if f.get("berufsabschluss","").lower() == "ja":
            field_map["rbtnBerufsabschluss"] = "/Ja"
        return fill_fillable(file_data, pdf_fields, field_map)

    if form_type == "ba_fw82":
        field_map = {}
        keyword_map = {
            "betrieb": f.get("firma",""), "vorname": f.get("vorname",""),
            "nachname": f.get("nachname",""), "gebdatum": f.get("geburtsdatum",""),
            "geburtsdatum": f.get("geburtsdatum",""), "svnr": f.get("svnr",""),
            "strasse": f.get("strasse",""), "plz": f.get("plz",""), "ort": f.get("ort",""),
            "tel": f.get("telefon",""), "email": f.get("email",""),
            "berufsbezeichnung": f.get("berufsbezeichnung",""),
        }
        for fname in pdf_fields:
            for kw, val in keyword_map.items():
                if kw in fname.lower():
                    field_map[fname] = val; break
        field_map["txtfErklaerungOrt"]   = f.get("ort","")
        field_map["txtfErklaerungDatum"] = heute
        return fill_fillable(file_data, pdf_fields, field_map)

    if form_type == "mannheim":
        pages = {
            1: [
                (120, 667, f.get("vorname","")), (270, 667, f.get("nachname","")),
                (120, 627, f.get("geburtsdatum","")), (120, 607, f.get("familienstand","")),
                (340, 607, f.get("staatsangehoerigkeit","")), (120, 547, f.get("kundennr","")),
                (120, 487, f.get("strasse","")),
                (120, 467, f"{f.get('plz','')} {f.get('ort','')}"),
                (120, 447, f.get("telefon","")), (120, 427, f.get("email","")),
                (330, 447, f.get("handy","")),
            ],
            3: [
                (120, 722, f.get("firma","")), (370, 722, f.get("betriebsnr","")),
                (120, 332, f.get("strasse","")),
                (120, 312, f"{f.get('plz','')} {f.get('ort','')}"),
                (120, 292, f.get("email","")),
            ],
            4: [
                (180, 752, f.get("eintritt","")),
                (55,  502, f.get("beschreibung","")[:100]),
                (250, 432, f.get("bildungstraeger","")),
                (250, 412, f"{f.get('massnahme_von','')} – {f.get('massnahme_bis','')}"),
                (370, 392, f.get("ustunden","")),
                (180, 372, f.get("massnahme_nr","")),
            ],
        }
        return fill_overlay(file_data, pages)

    # Generic
    if pdf_fields:
        field_map = {}
        value_map = {
            "vorname": f.get("vorname",""), "nachname": f.get("nachname",""),
            "name": f"{f.get('vorname','')} {f.get('nachname','')}".strip(),
            "geburt": f.get("geburtsdatum",""), "svnr": f.get("svnr",""),
            "strasse": f.get("strasse",""), "plz": f.get("plz",""), "ort": f.get("ort",""),
            "telefon": f.get("telefon",""), "mobil": f.get("handy",""),
            "email": f.get("email",""), "betrieb": f.get("firma",""),
            "firma": f.get("firma",""), "eintritt": f.get("eintritt",""),
            "berufsbezeichnung": f.get("berufsbezeichnung",""),
        }
        for fname in pdf_fields:
            for kw, val in value_map.items():
                if kw in fname.lower():
                    field_map[fname] = val; break
        return fill_fillable(file_data, pdf_fields, field_map)
    else:
        reader = PdfReader(io.BytesIO(file_data))
        ph = float(reader.pages[0].mediabox.height)
        pages = {1: [
            (120, ph-175, f"{f.get('vorname','')} {f.get('nachname','')}".strip()),
            (120, ph-215, f.get("geburtsdatum","")),
            (120, ph-355, f.get("strasse","")),
            (120, ph-375, f"{f.get('plz','')} {f.get('ort','')}"),
            (120, ph-415, f.get("email","")),
        ]}
        return fill_overlay(file_data, pages)


# ════════════════════════════════════════════════════════════════════════════
# STREAMLIT UI
# ════════════════════════════════════════════════════════════════════════════

st.title("📋 Erhebungsbogen Assistent")
st.caption("Lebenslauf hochladen → Bogen wird automatisch erkannt und ausgefüllt")

st.divider()

# ── SCHRITT 1: Dateien hochladen ─────────────────────────────────────────
st.subheader("① Dateien hochladen")
col1, col2 = st.columns(2)
with col1:
    cv_file = st.file_uploader("👤 Lebenslauf", type=["pdf","docx","doc"],
                                help="PDF oder Word-Datei")
with col2:
    form_file = st.file_uploader("📋 Erhebungsbogen", type=["pdf"],
                                  help="Beliebige Vorlage – wird automatisch erkannt")

if not (cv_file and form_file):
    st.info("Bitte beide Dateien hochladen, um fortzufahren.")
    st.stop()

# Formulardaten einlesen
cv_bytes   = cv_file.read()
form_bytes = form_file.read()

# Formulartyp erkennen
form_type, form_label, fillable, pdf_fields = detect_form(form_bytes)
fill_method = "Ausfüllbare Felder" if fillable else "Text-Overlay"
st.markdown(f'<div class="badge">🔍 Erkannter Bogentyp: <strong>{form_label}</strong> &nbsp;·&nbsp; {fill_method}</div>', unsafe_allow_html=True)

# CV-Daten extrahieren
with st.spinner("Lebenslauf wird ausgelesen…"):
    person = extract_cv(cv_bytes, cv_file.name)

st.divider()

# ── SCHRITT 2: Daten prüfen & ergänzen ──────────────────────────────────
st.subheader("② Erkannte Daten prüfen & ergänzen")

with st.expander("👤 Persönliche Daten", expanded=True):
    c1, c2 = st.columns(2)
    vorname      = c1.text_input("Vorname",     value=person.get("vorname",""))
    nachname     = c2.text_input("Nachname",    value=person.get("nachname",""))
    geburtsdatum = c1.text_input("Geburtsdatum (TT.MM.JJJJ)", value=person.get("geburtsdatum",""))
    geburtsname  = c2.text_input("Geburtsname (falls abw.)", value=person.get("geburtsname",""))
    geschlecht   = c1.selectbox("Geschlecht", ["","m","w","d"],
                                 index=["","m","w","d"].index(person.get("geschlecht","")) if person.get("geschlecht","") in ["m","w","d"] else 0)
    familienstand = c2.text_input("Familienstand", value=person.get("familienstand",""))
    staatsang    = c1.text_input("Staatsangehörigkeit", value=person.get("staatsangehoerigkeit",""))
    svnr         = c2.text_input("Sozialversicherungsnummer ⚠️", value=person.get("svnr",""),
                                  help="Nicht im Lebenslauf – bitte manuell eintragen")
    kundennr     = c1.text_input("Kundennummer (Agentur f. Arbeit)", value=person.get("kundennr",""))

with st.expander("🏠 Kontakt & Adresse", expanded=True):
    c1, c2 = st.columns(2)
    strasse  = c1.text_input("Straße + Hausnummer", value=person.get("strasse",""))
    plz      = c2.text_input("PLZ",  value=person.get("plz",""))
    ort      = c1.text_input("Ort",  value=person.get("ort",""))
    telefon  = c2.text_input("Telefon", value=person.get("telefon",""))
    handy    = c1.text_input("Handy",   value=person.get("handy",""))
    email    = c2.text_input("E-Mail",  value=person.get("email",""))

with st.expander("🎓 Ausbildung", expanded=True):
    c1, c2 = st.columns(2)
    bildung_opts = ["","Hauptschulabschluss","Realschulabschluss","Fachabitur","Abitur",
                    "Fachhochschulreife","Fachhochschule","Hochschule/Universität","Ohne Abschluss"]
    bildung_val  = person.get("bildungsabschluss","")
    bildung      = c1.selectbox("Bildungsabschluss", bildung_opts,
                                 index=bildung_opts.index(bildung_val) if bildung_val in bildung_opts else 0)
    berufsabs    = c2.selectbox("Berufsabschluss vorhanden?", ["","Ja","Nein"],
                                 index=["","Ja","Nein"].index(person.get("berufsabschluss","")) if person.get("berufsabschluss","") in ["Ja","Nein"] else 0)
    berufsbezeichnung = c1.text_input("Berufsbezeichnung", value=person.get("berufsbezeichnung",""))
    datum_zeugnis     = c2.text_input("Datum Zeugnis (MM.JJJJ)", value=person.get("datum_zeugnis",""))

with st.expander("🏢 Unternehmen & Beschäftigung", expanded=True):
    c1, c2 = st.columns(2)
    firma       = c1.text_input("Firmenname",    value=person.get("firma",""))
    betriebsnr  = c2.text_input("Betriebsnummer ⚠️", value="",
                                 help="Nicht im Lebenslauf – bitte manuell eintragen")
    eintritt    = c1.text_input("Eintrittsdatum (TT.MM.JJJJ)", value=person.get("eintrittsdatum",""))
    betriebsgroesse = c2.selectbox("Betriebsgröße", ["","< 50","50–499","500+"])

with st.expander("📚 Maßnahme / Weiterbildung", expanded=False):
    c1, c2 = st.columns(2)
    massnahme_nr    = c1.text_input("Maßnahme-Nummer ⚠️", value="",
                                     help="Wird benötigt – bitte manuell eintragen")
    bildungstraeger = c2.text_input("Bildungsträger", value="")
    massnahme_von   = c1.text_input("Beginn Maßnahme",  value="")
    massnahme_bis   = c2.text_input("Ende Maßnahme",    value="")
    ustunden        = c1.text_input("Unterrichtsstunden (mind. 120)", value="")
    beschreibung    = st.text_area("Qualifizierungsbedarf (Beschreibung)", value="", height=80)

# Fehlende Felder hinweisen
missing = []
if not svnr:         missing.append("Sozialversicherungsnummer")
if not massnahme_nr: missing.append("Maßnahme-Nummer")
if not betriebsnr:   missing.append("Betriebsnummer")
if not eintritt:     missing.append("Eintrittsdatum")
if missing:
    st.markdown(f'<div class="missing">⚠️ Noch nicht ausgefüllt: <strong>{", ".join(missing)}</strong></div>',
                unsafe_allow_html=True)

st.divider()

# ── SCHRITT 3: PDF generieren ────────────────────────────────────────────
st.subheader("③ Ausgefüllten Bogen herunterladen")

if st.button("✍️ Bogen automatisch befüllen", type="primary", use_container_width=True):
    fields = {
        "vorname": vorname, "nachname": nachname, "geburtsdatum": geburtsdatum,
        "geburtsname": geburtsname, "geschlecht": geschlecht, "familienstand": familienstand,
        "staatsangehoerigkeit": staatsang, "svnr": svnr, "kundennr": kundennr,
        "strasse": strasse, "plz": plz, "ort": ort, "telefon": telefon,
        "handy": handy, "email": email, "bildungsabschluss": bildung,
        "berufsabschluss": berufsabs, "berufsbezeichnung": berufsbezeichnung,
        "datum_zeugnis": datum_zeugnis, "firma": firma, "betriebsnr": betriebsnr,
        "betriebsgroesse": betriebsgroesse, "eintritt": eintritt,
        "massnahme_nr": massnahme_nr, "bildungstraeger": bildungstraeger,
        "massnahme_von": massnahme_von, "massnahme_bis": massnahme_bis,
        "ustunden": ustunden, "beschreibung": beschreibung,
    }
    with st.spinner("Bogen wird befüllt…"):
        try:
            pdf_bytes_out = build_filled_pdf(form_bytes, form_type, pdf_fields, fields)
            fname = re.sub(r'[^\w.\-]', '_',
                f"{nachname}_{vorname}_Erhebungsbogen_{datetime.today().strftime('%Y%m%d')}.pdf")
            st.success("✅ Bogen erfolgreich ausgefüllt!")
            st.download_button(
                label=f"⬇️ {fname} herunterladen",
                data=pdf_bytes_out,
                file_name=fname,
                mime="application/pdf",
                use_container_width=True,
            )
        except Exception as e:
            st.error(f"❌ Fehler: {e}")

st.divider()
st.caption("Erhebungsbogen Assistent · Alle Daten werden nur lokal verarbeitet · Keine Daten werden gespeichert")
