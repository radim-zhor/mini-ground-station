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

Až po tom, co proběhly migrace:

```bash
pg_restore --no-owner --data-only -d "$NEW_DATABASE_URL" ~/gs-backup.dump
```

`--data-only` proto, že schéma už vytvořil Alembic; `--no-owner` proto, že
vlastník role se mezi Renderem a Neonem liší.

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
- **Zálohuj.** Neon free nemá point-in-time restore delší než pár dní. Data jsou
  řádově kilobajty, takže denní dump z Macu stačí:

  ```bash
  pg_dump "$DATABASE_URL" -Fc -f ~/gs-backups/gs-$(date +%F).dump
  ```

## Co se ztratí, když záloha není

Historie přeletů v `contacts` a poslední pozice stanice v `station_status`.
Nic z toho není nenahraditelné: stanice si pozici zjistí sama při příštím
běhu (`OBSERVER_MODE=auto`) a nové přelety začnou přibývat hned. Přijdeš
o historii, ne o funkčnost.
