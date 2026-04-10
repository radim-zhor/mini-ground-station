# V-dipól pro příjem NOAA APT (137 MHz)

Nejjednodušší anténa pro příjem NOAA satelitů. Výroba zabere ~20 minut.

## Co budeš potřebovat

- Tuhý drát nebo tyčka — např. rozvinutý drátěný věšák nebo měděný drát 2.5 mm²
- Kousek koaxiálního kabelu (RG-58 nebo RG-174), délka dle potřeby
- SMA konektor (nebo přímo RTL-SDR na konci kabelu)
- Pájecí souprava nebo svorky

---

## Rozměry

Frekvence NOAA APT: ~137.5 MHz

| Část | Délka |
|---|---|
| Každé rameno | **54 cm** (λ/4) |
| Celková délka | 108 cm |

---

## Tvar

```
    \         /
     \       /
      \     /
       \   /    ← úhel 120° mezi rameny (měřeno od středu dolů)
        \ /
         |
       koax
```

Ramena míří **dolů a do stran** pod úhlem 120° od sebe. Tím se anténa přiblíží kruhové polarizaci — stejné jako APT signál NOAA satelitů.

---

## Zapojení

```
rameno A ──── střed koaxu (inner conductor)
rameno B ──── oplet koaxu (shield/ground)
```

Oba spoje dobře zapájej nebo upevni svorkou. Kontakt opletu s ramenem B je stejně důležitý jako střed.

---

## Umístění a orientace

- **Venku nebo přímo v okně** bez skla mezi anténou a oblohou — zeď nebo sklo na 137 MHz výrazně tlumí signál
- Anténa míří rameny dolů, koax dolů — střed antény nahoru
- **Není třeba směrovat** — V-dipól pokrývá celý horizont, satelit si projde sám
- Čím výš nad zemí, tím lepší výhled na nízko letící satelity (el < 20°)

---

## Tipy

- Přelety s max. elevací nad 20° dávají čitelné snímky, nad 30° jsou výborné
- Pokud nemáš koax, zkus anténu co nejblíže k RTL-SDR donglu a použij krátký adaptér
- PPM korekce: RTL2838 bývá o ±50–100 ppm — pokud je signál posunutý, uprav v SDR++ PPM Correction

---

## Příklad z věšáku

1. Rozstřihni drátěný věšák na dva kusy po 54 cm
2. Ohni každý kus do rovné tyčky
3. Připájej ke koaxu (viz zapojení výše)
4. Roztáhni ramena do úhlu ~120° a upevni (páska, svorka, lepidlo)
5. Pověs za koax na okno nebo ven

Hotovo.
