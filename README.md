# DVD AutoRip

Vložíš DVD, aplikace ho vyripuje (MakeMKV), překóduje (HandBrake), pojmenuje přes OMDb/TMDB,
uloží do složky Jellyfinu a pošle notifikaci do Home Assistantu.

## Požadavky

- Windows 10/11
- Python 3.x
- MakeMKV (`makemkvcon64.exe`)
- HandBrake CLI (`HandBrakeCLI.exe`)

## Rychlý start

1. Nainstalujte Python 3 a Flask:
   ```bat
   pip install flask
   ```
2. Zkopírujte `config.example.json` na `config.json`.
3. Vyplňte v `config.json` cesty a klíče.
4. Spusťte:
   ```bat
   run.bat
   ```
   nebo `python app.py`, pak otevřete http://localhost:8080

## API klíče (zdarma)

Aplikace podporuje **OMDb** (doporučeno) a **TMDB**. Klíče jsou zdarma:

- **OMDb**: https://www.omdbapi.com/apikey.aspx → FREE (1000 req/den) → potvrďte e-mail.
- **TMDB**: https://www.themoviedb.org/ → účet → Nastavení → API → v3 auth API Key.

Vložte je do `config.json`:
```json
{
  "omdb_key": "VÁŠ_OMDB_KLÍČ",
  "tmdb_key": "VÁŠ_TMDB_KLÍČ"
}
```

`config.json` je v `.gitignore` – **nikdy ho necommitujte** (obsahuje vaše klíče a cesty).

## Konfigurace

Všechny volby jsou v `config.json` (viz `config.example.json`). Důležité:

- `drive` – písmeno mechaniky (např. `D`)
- `tmp` – pracovní složka
- `out` / `out_kids` – cílové složky (Jellyfin)
- `minlength` – minimální délka titulu v sekundách (výchozí 2400)
- `dynamic_minlength` – automaticky odvodit minimum z disku (doporučeno `true`)
- `debug` – zapnout `/debug` a `/api/debug`

## Bezpečnost

- `config.json` je ignorován Gitem. Před commitem zkontrolujte `git status`.
- API klíče si každý uživatel pořizuje sám (zdarma).
- Nikdy nesdílejte svůj `config.json`.

## Licence

Používejte v souladu s podmínkami MakeMKV/HandBrake a autorskými právy k médiím.