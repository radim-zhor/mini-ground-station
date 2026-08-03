# Migrace databáze z Render free Postgres na Neon

Render free PostgreSQL **expiruje po 30 dnech** a instance se smaže. Neon má
free tier bez expirace (0,5 GB), takže odpadá měsíční obnovování. Aplikace čte
databázi výhradně přes `DATABASE_URL` (`app/database.py`) a schéma vlastní
Alembic, takže migrace je **změna jedné proměnné prostředí — nula řádků kódu**.

## Než začneš: zachraň stará data

Jen pokud je expirovaná Render instance ještě dostupná (Render ji chvíli drží,
než ji smaže). Connection string je v Render dashboardu u databáze:

```bash
pg_dump "$OLD_DATABASE_URL" -Fc -f ~/gs-backup.dump
```

Když už se připojit nejde, nic se neděje — viz *Co se ztratí* na konci.

## 1. Založ projekt na Neonu

1. neon.tech → Sign up (GitHub) → **Create project**
2. Region: **AWS eu-central-1 (Frankfurt)** — nejblíž Renderu ve Frankfurtu
   i tobě, ušetří desítky ms na každý dotaz.
3. Postgres 16 nebo 17, obojí funguje.
4. Zkopíruj **connection string** (Dashboard → Connect).

Vypadá takhle:

```
postgresql://neondb_owner:HESLO@ep-neco-123456.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

Pozor na dvě věci:

- Musí začínat `postgresql://`, ne `postgres://` — SQLAlchemy druhý tvar
  neakceptuje. Neon dává správný tvar sám.
- Když by připojení hlásilo problém s `channel_binding`, umaž z URL
  `&channel_binding=require`. `sslmode=require` tam nech.

## 2. Nastav DATABASE_URL na Renderu

Ve web service → **Environment** → `DATABASE_URL` přepiš na neonský string.

Pokud je `DATABASE_URL` napojená přes *Add from database* na starou Render
databázi, tuhle vazbu nejdřív smaž a přidej proměnnou ručně — jinak ji Render
při deployi přepíše zpátky.

Zkontroluj, že Start Command (nebo Pre-Deploy Command) pořád obsahuje migraci:

```
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## 3. Deploy a ověření

Redeploy. V logu musí projet celý řetězec `0001_baseline` … `0005_add_pass_events`.

Lokálně se dá stav ověřit i bez deploye:

```bash
DATABASE_URL="postgresql://...neon.tech/neondb?sslmode=require" .venv/bin/alembic current
```

Musí vypsat `0005_add_pass_events (head)`.

> Lokální `.venv` nemá nainstalovaný `psycopg2` (běžný dev jede na SQLite).
> Pro tenhle příkaz doinstaluj: `.venv/bin/pip install psycopg2-binary`.

## 4. Obnov data ze zálohy (pokud ji máš)

Až po tom, co proběhly migrace. **Restoruj výhradně datové tabulky** — dump
obsahuje i `alembic_version`, kde už po migraci řádek je, a naivní
`pg_restore --data-only` bez `-t` skončí na
`duplicate key value violates unique constraint "alembic_version_pkc"`:

```bash
pg_restore --no-owner --no-acl --data-only \
    -t contacts -t station_status -d "$NEW_DATABASE_URL" ~/gs-backup.dump
```

`--data-only` proto, že schéma už vytvořil Alembic; `--no-owner` a `--no-acl`
proto, že vlastník role se mezi providery liší.

Pak **musíš dorovnat sekvence**. `-t` vybere data tabulek, ale ne TOC položky
`SEQUENCE SET`, takže `contacts_id_seq` zůstane na 1, zatímco nejvyšší `id`
je třeba 68 — a první nový přelet by spadl na `contacts_pkey`:

```bash
psql "$NEW_DATABASE_URL" \
    -c "SELECT setval('contacts_id_seq', (SELECT max(id) FROM contacts));" \
    -c "SELECT setval('station_status_id_seq', (SELECT max(id) FROM station_status));"
```

Ověřeno 3. 8. 2026 proti PostgreSQL 18: po setval dostal nově vložený kontakt
`id 69` a data zůstala netknutá.

> `pg_dump`, `pg_restore` a `psql` na macOS dodává Homebrew balíček `libpq`,
> který je keg-only — nejsou v PATH, volej je přes
> `/opt/homebrew/opt/libpq/bin/`. Verze klienta musí být **stejná nebo vyšší
> než verze serveru** (Render jel PostgreSQL 18).

## 5. Restartuj agenta

Kontakty, které se během výpadku nepodařilo odeslat, leží ve frontě
`pending.db` (`agent/client.py`) a `retry_pending()` je zkusí znovu **při
startu agenta**. Takže po zprovoznění databáze agenta restartuj — ale
**nikdy uprostřed přeletu**, viz CLAUDE.md.

## Ověřeno

Řetězec migrací proběhl proti čisté PostgreSQL 16 (Docker, 3. 8. 2026):
všech pět revizí prošlo, výsledkem jsou tabulky `contacts`, `station_status`,
`alembic_version`, unikátní constraint `uq_contact_pass (satellite, aos)`
a všech 15 sloupců `contacts` včetně `telemetry` / `events` (typ `json`).
Druhý běh `alembic upgrade head` je no-op. Nic v migracích není
PostgreSQL-specifické, takže stejný postup platí i pro jiného providera.

## Na co si dát pozor

- **Scale-to-zero.** Neon free uspí compute po ~5 minutách nečinnosti a probouzí
  ho na první dotaz (stovky ms). Pro stránku, kterou frontend poolinguje po 5 s,
  je to neviditelné; projeví se to jen na prvním načtení po delší pauze.
- **`init_db()` volá `create_all()` při importu** (`app/main.py:17`). Kdyby
  uvicorn nastartoval dřív než Alembic, tabulky vzniknou bez záznamu ve
  `alembic_version`. Není to průšvih — všechny migrace mají guard a jsou
  no-op — ale je to důvod, proč `alembic upgrade head` patří **před** uvicorn.
- **Snímky nejsou v databázi.** PNG z Meteoru se ukládají do
  `app/static/images/` (`app/routes/contacts.py:27`), tedy na disk web service.
  Na free tieru je disk ephemerální, takže se obrázky ztratí při každém deployi
  bez ohledu na to, kde je databáze. Řádky v `contacts` zůstanou, jen odkazují
  na neexistující soubor. Řeší se to až externím úložištěm (R2/S3), ne Neonem.
- **Zálohuj.** Neon free nemá dlouhé point-in-time restore. Viz *Týdenní záloha*
  níž — je hotová, stačí ji zapnout.

## Co se ztratí, když záloha není

Historie přeletů v `contacts` a poslední pozice stanice v `station_status`.
Nic z toho není nenahraditelné: stanice si pozici zjistí sama při příštím
běhu (`OBSERVER_MODE=auto`) a nové přelety začnou přibývat hned. Přijdeš
o historii, ne o funkčnost.

## Týdenní záloha

`tools/gs-backup.sh` dumpne databázi jednou týdně na Mac. Nic z toho nesmí
ležet v `~/Documents` — ten adresář je pod ochranou macOS TCC a proces
spuštěný launchd tam nesmí (viz `docs/bezobsluzny-provoz.md`), takže skript
běží z kopie v `~/.local/bin/`. Repo zůstává zdrojem pravdy; po každé úpravě
skriptu je potřeba tu kopii obnovit.

| Co | Kde |
|---|---|
| Zdroj skriptu (verzovaný) | `tools/gs-backup.sh` |
| Spouštěná kopie | `~/.local/bin/gs-backup.sh` |
| launchd job | `~/Library/LaunchAgents/com.radimzhor.gs-backup.plist` |
| Zálohy a log | `~/gs-backups/` |
| Přihlašovací údaje | `~/.config/ground-station/db.env` (chmod 600) |

### Zapnutí

Nejdřív připojovací řetězec do souboru, který přečte jen tvůj účet — heslo se
tak nedostane do skriptu ani do historie shellu:

```bash
umask 077 && read -rs "U?Neon URL: " && printf 'DATABASE_URL="%s"\n' "$U" > ~/.config/ground-station/db.env && unset U && chmod 600 ~/.config/ground-station/db.env
```

Zkušební běh a zapnutí plánovače:

```bash
bash ~/.local/bin/gs-backup.sh && tail -2 ~/gs-backups/backup.log
launchctl unload ~/Library/LaunchAgents/com.radimzhor.gs-backup.plist 2>/dev/null; launchctl load ~/Library/LaunchAgents/com.radimzhor.gs-backup.plist && launchctl list | grep gs-backup
```

Po úpravě `tools/gs-backup.sh` v repu:

```bash
install -m 755 tools/gs-backup.sh ~/.local/bin/gs-backup.sh
```

### Co skript hlídá

Běží v neděli ve 3:00, drží 8 posledních záloh (~2 měsíce) a loguje do
`~/gs-backups/backup.log`. Když je nastavená `NTFY_TOPIC`, pošle při selhání
notifikaci na stejný kanál, který používá agent.

Dumpuje do `.partial` a přejmenuje až po ověření, takže nedokončený běh nikdy
nepřepíše poslední dobrou zálohu. Ověření **počítá řádky v COPY bloku**, ne
velikost souboru — prázdná ale zmigrovaná databáze dá platný dump o 6710 B,
který projde jak kontrolou velikosti, tak `pg_restore -l | grep TABLE DATA`,
protože pg_dump vypisuje `TABLE DATA` i pro tabulku bez řádků. Bez počítání
řádků by se rok zálohovalo prázdno a poznalo by se to až při obnově.

Ověřeno 3. 8. 2026 proti PostgreSQL 18, všech pět cest:

| Situace | Výsledek |
|---|---|
| Databáze s daty | exit 0, `OK … (25961 B, 68 kontaktů)` |
| Prázdná (jen zmigrovaná) databáze | exit 1, `dump neobsahuje ani jeden kontakt`, soubor nevznikl |
| Databáze nedostupná | exit 1, žádný `.partial` nezůstal, předchozí záloha netknutá |
| Chybějící `db.env` | exit 1, `chybí …/db.env` |
| 11 záloh v adresáři | zůstalo 8 nejnovějších |

### Poslední záloha z Renderu

`~/gs-backups/render-final-2026-08-03.dump` je stav Render databáze těsně
před jejím smazáním (68 kontaktů, 14.–27. 7.). Je **záměrně pojmenovaná mimo
vzor `gs-*.dump`**, aby ji rotace nesmazala, a je jen pro čtení.
