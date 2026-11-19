## Prohlídka

- Řádek začínající lomítkem je příkaz, např. `/help`. Po napsání `/` se otevře nabídka příkazů; ↑/↓ vybírá, Tab nebo → doplní, Enter spustí, Esc nabídku zavře. Za příkazem nabídka ukáže jeho volby.
- Cokoli jiného je rozhovor se mnou.
- `/help` vypíše příkazy, `/help <příkaz>` ukáže jeho volby.
- `/settings` ukáže a mění nastavení (jazyk, poskytovatel, model).
- `/setup` znovu projde nastavení modelu.
- `/tour` ukáže tuto prohlídku kdykoli.
- `/add-information` mi o tobě řekne: soubory a odkazy (`.txt`, `.md`, `.pdf`, `.html`, webové stránky), krátké představení a moje otázky na to, co ještě chybí. `/add-information --source <soubor|url>` přidá zdroje bez otázek.
- `/show-profile` ukáže, co o tobě vím, tvoje zdroje, vygenerovaný soubor filtru a kde je který soubor.
- Webové stránky a soubory `.html` nejdřív projdou kontrolou na injekci; co je podezřelé, jde do šifrované karantény. `/show-quarantine` ji vypíše, `/release-quarantine <id>` obsah pustí dál po tvém ano.
- `/exit` nebo Ctrl-D ukončí aplikaci. Ctrl-C zruší rozepsaný řádek nebo běžící příkaz.
- Příkazy jdou spouštět i bez chatu: `sortingcap run <příkaz>`, například z cronu. `setup` jen v chatu.

## Co dál

- Začni příkazem `/add-information`: dej mi svůj životopis a odpověz na pár otázek. Výsledek pak zkontroluj příkazem `/show-profile`.
- V dalších verzích se naučím projít pracovní nabídky (`/scrape-jobs`) a psát dopisy.
