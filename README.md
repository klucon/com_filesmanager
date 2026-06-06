# com_filesmanager

Moderní správce souborů pro **KLUCON CMS**.

Komponenta poskytuje kompletní správu souborů v admin rozhraní – uzamčenou
(sandbox) do jednoho kořenového adresáře. Žádná operace nesmí uniknout mimo
tento kořen.

## Funkce

- **Procházení** složek s breadcrumb navigací, řazením a souhrnem obsahu
- **Upload** více souborů najednou včetně drag & drop
- **Stahování** souborů; složky se stáhnou jako ZIP
- **Přejmenování, přesun, kopírování** (jednotlivě i hromadně přes výběr)
- **Koš** – mazání přesouvá do koše s možností obnovy nebo trvalého smazání
- **Hledání** souborů a složek (rekurzivně)
- **Náhledy** obrázků a PDF
- **ZIP / rozbalení** archivů (s ochranou proti zip-slip)
- **Textový / kódový editor** přímo v adminu (strop 2 MB)
- **Sdílecí odkazy** s expirací a limitem stažení (veřejné stahování bez přihlášení)
- **Audit log** všech akcí
- **ACL na úrovni rolí** – oprávnění `filesmanager.view / manage / upload / delete / edit / share`

## Kořenový adresář

Spravovaný kořen je ve výchozím stavu `storage/files` (vytvoří se automaticky).
Lze ho přepsat nastavením `FILESMANAGER_DIR` v konfiguraci CMS.

## Bezpečnost

- Každá cesta je normalizovaná a ověřená, že leží uvnitř kořene (ochrana proti `..`).
- Rozbalení ZIP kontroluje cílové cesty (ochrana proti zip-slip).
- Editor povoluje pouze textové přípony a omezuje velikost souboru.
- Veřejné sdílení funguje pouze pro soubory, respektuje expiraci, limit stažení a zneplatnění.

## Instalace

Komponenta se instaluje přes marketplace KLUCON CMS jako standardní balíček
rozšíření (`com_filesmanager-<verze>.zip`).

## Vývoj

```bash
pip install -e ".[dev]"
ruff check .
pytest -q
```

## Licence

MIT
