import pytest

from omega.composition import pack_size, resolve

CAP = {"count": 120, "unit": "capsule", "multi": 1}

CASES = [
    # Row table with a "pour 4 capsules" header and "750 mg EPA" that must not be read backwards.
    ("Arkogélules Oméga 3",
     "INFORMATION NUTRITIONNELLE MOYENNE : pour 4 capsules Huile de poisson : 2080 mg "
     "Concentration en : Oméga 3 : 750 mg EPA : 400 mg DHA : 250 mg Vitamine E",
     CAP, 100, 62.5),
    # Column table: labels first, then values.
    ("Granions Oméga 3 1000 mg capsules",
     "Actifs Teneur pour 2 capsules AR* Huile de poisson 1000 mg Oméga 3 Dont EPA Dont DHA "
     "800 mg 400 mg 300 mg -",
     CAP, 200, 150),
    ("Super Strength Omega 3", "Chaque capsule contient 330 mg d’EPA et 220 mg de DHA.", CAP, 330, 220),
    ("Natural Health Omega 3 35 Dha/25 Epa Tg Ifos 1000 mg",
     "Cada cápsula de 1.000 mg contiene un 35 % de EPA y un 25 % de DHA.", CAP, 350, 250),
    ("Omega 3 para el Corazón Natural EPA",
     "INFORMACIÓN NUTRICIONAL Ingrediente Por 2 perlas Omega-3 total* 1.600 mg — EPA (ácido "
     "eicosapentaenoico) 1000 mg — DHA (ácido docosahexaenoico) 500 mg", CAP, 500, 250),
    ("Omega-3 infantil líquido",
     "INFORMACIÓN NUTRICIONAL Ingrediente Por 5 mL (1 cucharada de postre) Omega-3 total* 1.390 mg "
     "— EPA (ácido eicosapentaenoico) 740 mg — DHA (ácido docosahexaenoico) 460 mg",
     {"count": 150, "unit": "ml", "multi": 1}, 148, 92),
    ("Eglé Docovision DHA",
     "Tomar de 1 a 4 cápsulas al día. Composición por 4 cápsulas: 3840mg DHA TG, 240mg EPA TG, "
     "4mg astaxantina", CAP, 60, 960),
    ("Omega 3 1000 mg Drasanvi",
     "El efecto beneficioso se obtiene con una ingesta diaria de 250 mg de EPA y DHA. "
     "Las perlas aportan 180 mg de EPA y 120 mg de DHA por perla.", CAP, 180, 120),
    ("Oméga 3 origine 2000mg - 120 capsules",
     "Liste des ingrédients Huile de poisson source d'omega 3 2 000 mg EPA (acide "
     "eicosapentaénoïque) 600 mg DHA (acide docosahexaénoïque) 400 mg Voici ce que contient "
     "chaque gélule. Conseils d'utilisation 2 à 4 capsules par jour, à avaler avec un verre d'eau.",
     CAP, 150, 100),
    ("Norsan Omega-3 Total 120 capsules",
     "4 capsules par jour à prendre pendant ou après un repas. Une dose journalière contient 1500 mg "
     "d’acides gras oméga-3, dont 707 mg d’EPA et 368 mg de DHA", CAP, 176.8, 92),
]


@pytest.mark.parametrize("name,text,pack,epa,dha", CASES, ids=[c[0][:30] for c in CASES])
def test_resolve(name, text, pack, epa, dha):
    c = resolve(name, None, text, pack)
    assert c, "no reading"
    assert c["epa"] == pytest.approx(epa, abs=0.6)
    assert c["dha"] == pytest.approx(dha, abs=0.6)


def test_boilerplate_only_gives_nothing():
    text = "El efecto beneficioso se obtiene con una ingesta diaria de 250 mg de EPA y DHA."
    assert resolve("Omega 3", None, text, CAP) is None


@pytest.mark.parametrize("name,count,unit", [
    ("Pranarôm Omega 3 Forte, 2X60 Cápsulas", 120, "capsule"),
    ("Arkopharma Arkogélules Oméga 3 180 Capsules + 60 Capsules Offertes", 240, "capsule"),
    ("Möller's Omega-3 Aceite Hígado De Bacalao 250ml", 250, "ml"),
    ("Zenement Omega-3 Premium, Pack 2 x 120 cápsulas blandas", 240, "capsule"),
    ("Oméga 3 premium Epax® - 180 capsules - Onatera", 180, "capsule"),
    ("Omega 3 Gummies 60 gominolas", 60, "gummy"),
])
def test_pack_size(name, count, unit):
    p = pack_size(name)
    assert p["count"] == count and p["unit"] == unit


def test_basis_after_amounts():
    text = ("Huile de poisson ultra pure (90% de triglycérides). 800 mg d’EPA et 600 mg de DHA par dose "
            "journalière (4 capsules).")
    c = resolve("Oméga 3 Epax", None, text, CAP)
    assert (c["epa"], c["dha"], c["confidence"]) == (200, 150, "high")


def test_oil_weight_is_not_epa_dha_total():
    text = ("Ingrédients pour 4 capsules : Huile de poisson Omegavie concentrée riche en acides gras "
            "essentiels (EPA et DHA) 2708 mg, capsule (gélatine de poisson).")
    assert resolve("Fleurance Nature Oméga 3 60 Capsules", None, text, CAP) is None


def test_pair_beats_combined_label():
    text = ("Ingredientes por cápsula blanda: Aceite de pescado azul rico en Omega 3 35/25 E.P.A/DHA EE "
            "(1000 mg): EPA (ácido eicosapentaenoico, 350mg), DHA (acido docosahexaenoico, 250mg).")
    c = resolve("H4U Omega 3 Concentrado 30 cápsulas", None, text, CAP)
    assert (c["epa"], c["dha"]) == (350, 250)
