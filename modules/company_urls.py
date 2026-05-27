"""
Słownik stron kariery znanych firm.
Klucze: fragment nazwy firmy (lowercase, bez ogonków).
get_company_url(name) zwraca URL lub "" dla nieznanych firm.
"""
import re

_CAREER_URLS: dict[str, str] = {
    # ── Banki polskie ──────────────────────────────────────────────────────────
    "ing":              "https://kariera.ing.pl/",
    "commerzbank":      "https://www.commerzbank.com/karriere/",
    "santander":        "https://www.santanderbankpolska.pl/kariera",
    "mbank":            "https://kariera.mbank.pl/",
    "pko":              "https://kariera.pkobp.pl/",
    "bnp paribas":      "https://kariera.bnpparibas.pl/",
    "bnp":              "https://kariera.bnpparibas.pl/",
    "credit agricole":  "https://kariera.credit-agricole.pl/",
    "alior":            "https://kariera.aliorbank.pl/",
    "millennium":       "https://kariera.bankmillennium.pl/",
    "pekao":            "https://kariera.pekao.com.pl/",
    "bgk":              "https://kariera.bgk.pl/",
    "getin":            "https://kariera.getinbank.pl/",

    # ── Banki międzynarodowe ───────────────────────────────────────────────────
    "hsbc":             "https://www.hsbc.com/careers",
    "citi":             "https://jobs.citi.com/",
    "citibank":         "https://jobs.citi.com/",
    "deutsche bank":    "https://careers.db.com/",
    "raiffeisen":       "https://www.rbinternational.com/en/careers.html",
    "credit suisse":    "https://www.credit-suisse.com/careers",
    "jpmorgan":         "https://careers.jpmorgan.com/",
    "jp morgan":        "https://careers.jpmorgan.com/",
    "barclays":         "https://search.jobs.barclays/",
    "goldman sachs":    "https://www.goldmansachs.com/careers/",
    "goldman":          "https://www.goldmansachs.com/careers/",
    "morgan stanley":   "https://www.morganstanley.com/people-opportunities",
    "wells fargo":      "https://www.wellsfargo.com/about/careers/",
    "bmo":              "https://jobs.bmo.com/",
    "bny mellon":       "https://bnymellon.com/us/en/careers.html",
    "ubs":              "https://www.ubs.com/global/en/careers.html",
    "ing hubs":         "https://kariera.ing.pl/",

    # ── Fintech / Payments ────────────────────────────────────────────────────
    "payu":             "https://careers.payu.com/",
    "revolut":          "https://www.revolut.com/careers/",
    "stripe":           "https://stripe.com/jobs",
    "klarna":           "https://www.klarna.com/careers/",
    "adyen":            "https://www.adyen.com/careers",
    "wise":             "https://www.wise.jobs/",
    "paypal":           "https://careers.pypl.com/",
    "monzo":            "https://monzo.com/careers/",
    "n26":              "https://n26.com/en-eu/careers",
    "checkout":         "https://www.checkout.com/careers",
    "paysafe":          "https://careers.paysafe.com/",
    "worldline":        "https://workingat.worldline.com/",
    "mastercard":       "https://careers.mastercard.com/",
    "visa":             "https://careers.visa.com/",
    "american express": "https://jobs.americanexpress.com/",
    "amex":             "https://jobs.americanexpress.com/",

    # ── Big Tech ──────────────────────────────────────────────────────────────
    "google":           "https://careers.google.com/",
    "microsoft":        "https://careers.microsoft.com/",
    "amazon":           "https://www.amazon.jobs/",
    "meta":             "https://www.metacareers.com/",
    "facebook":         "https://www.metacareers.com/",
    "apple":            "https://www.apple.com/careers/",
    "netflix":          "https://jobs.netflix.com/",
    "spotify":          "https://www.lifeatspotify.com/jobs",
    "salesforce":       "https://careers.salesforce.com/",
    "oracle":           "https://www.oracle.com/pl/corporate/careers/",
    "sap":              "https://jobs.sap.com/",
    "ibm":              "https://www.ibm.com/employment/",
    "intel":            "https://jobs.intel.com/",
    "cisco":            "https://jobs.cisco.com/",
    "vmware":           "https://careers.vmware.com/",
    "broadcom":         "https://careers.broadcom.com/",
    "atlassian":        "https://www.atlassian.com/company/careers",
    "zendesk":          "https://jobs.zendesk.com/",
    "twilio":           "https://www.twilio.com/company/jobs",
    "datadog":          "https://careers.datadoghq.com/",
    "snowflake":        "https://careers.snowflake.com/",
    "calendly":         "https://careers.calendly.com/",
    "smartsheet":       "https://www.smartsheet.com/careers",
    "shopify":          "https://www.shopify.com/careers",
    "uber":             "https://www.uber.com/global/en/careers/",
    "airbnb":           "https://careers.airbnb.com/",
    "twitter":          "https://careers.twitter.com/",
    "linkedin":         "https://careers.linkedin.com/",
    "slack":            "https://slack.com/intl/en-pl/careers",
    "zoom":             "https://careers.zoom.us/",
    "hubspot":          "https://www.hubspot.com/careers",
    "intercom":         "https://www.intercom.com/careers",
    "livekit":          "https://livekit.io/careers",
    "automox":          "https://www.automox.com/company/careers",
    "oportun":          "https://oportun.com/about/careers/",
    "yipitdata":        "https://yipitdata.com/careers/",
    "radformation":     "https://radformation.com/careers",
    "cosuno":           "https://cosuno.com/en/careers",
    "ryder":            "https://ryder.com/en-us/careers",
    "affirm":           "https://www.affirm.com/careers",
    "cohesity":         "https://www.cohesity.com/company/careers/",
    "spiralyze":        "https://spiralyze.com/careers/",
    "infuse":           "https://infuse.com/careers",

    # ── Consulting / Outsourcing ───────────────────────────────────────────────
    "accenture":        "https://www.accenture.com/pl-pl/careers",
    "capgemini":        "https://www.capgemini.com/pl-pl/kariera/",
    "deloitte":         "https://apply.deloitte.com/",
    "pwc":              "https://www.pwc.pl/pl/kariera.html",
    "kpmg":             "https://kariera.kpmg.pl/",
    "ey":               "https://careers.ey.com/",
    "ernst young":      "https://careers.ey.com/",
    "mckinsey":         "https://www.mckinsey.com/careers/",
    "bcg":              "https://careers.bcg.com/",
    "cognizant":        "https://careers.cognizant.com/",
    "infosys":          "https://career.infosysit.com/",
    "wipro":            "https://careers.wipro.com/",
    "atos":             "https://atos.net/en/careers",
    "fujitsu":          "https://fujitsu.com/global/about/jobs/",
    "dxc":              "https://jobs.dxc.com/",
    "luxoft":           "https://career.luxoft.com/",
    "epam":             "https://www.epam.com/careers",
    "globallogic":      "https://www.globallogic.com/career/",
    "softserve":        "https://career.softserveinc.com/",

    # ── Telco ─────────────────────────────────────────────────────────────────
    "orange":           "https://kariera.orange.pl/",
    "t-mobile":         "https://kariera.t-mobile.pl/",
    "tmobile":          "https://kariera.t-mobile.pl/",
    "play":             "https://kariera.play.pl/",
    "plus":             "https://kariera.plus.pl/",
    "polkomtel":        "https://kariera.plus.pl/",

    # ── Polska IT / e-commerce ─────────────────────────────────────────────────
    "allegro":          "https://allegro.pl/kariera",
    "asseco":           "https://kariera.asseco.com/",
    "comarch":          "https://kariera.comarch.pl/",
    "inpost":           "https://kariera.inpost.pl/",
    "cd projekt":       "https://careers.cdprojektred.com/",
    "cdprojekt":        "https://careers.cdprojektred.com/",
    "itmagination":     "https://itmagination.com/careers",
    "ailleron":         "https://ailleron.com/pl/kariera/",

    # ── Healthcare / Pharma ───────────────────────────────────────────────────
    "roche":            "https://www.roche.com/careers/",
    "pfizer":           "https://www.pfizercareers.com/",
    "novartis":         "https://www.novartis.com/careers/",
    "philips":          "https://www.careers.philips.com/",
    "siemens healthineers": "https://www.siemens-healthineers.com/careers",
    "johnson":          "https://jobs.jnj.com/",
    "abbvie":           "https://careers.abbvie.com/",
    "astrazeneca":      "https://careers.astrazeneca.com/",
    "university of kansas": "https://employment.ku.edu/",
    "clinChoice":       "https://www.clinchoice.com/about-us/careers/",
}

_NOISE = re.compile(
    r"\b(sp\.?\s*z\.?\s*o\.?\s*o\.?|s\.?\s*a\.?|gmbh|ltd\.?|inc\.?|corp\.?|"
    r"llc|plc|ag|se|holding|group|polska|poland|hubs|technologies|technology|"
    r"solutions|services|systems|digital|software|consulting|it|bank)\b",
    re.IGNORECASE,
)


def _normalize(name: str) -> str:
    name = name.lower()
    name = _NOISE.sub("", name)
    name = re.sub(r"[^\w\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def get_company_url(company_name: str) -> str:
    """Zwraca URL strony kariery firmy lub '' jeśli nieznana."""
    if not company_name:
        return ""
    norm = _normalize(company_name)

    # Dokładne dopasowanie
    if norm in _CAREER_URLS:
        return _CAREER_URLS[norm]

    # Dopasowanie częściowe – klucz zawarty w nazwie lub odwrotnie
    for key, url in _CAREER_URLS.items():
        if key in norm or (len(key) > 4 and key in company_name.lower()):
            return url

    return ""
