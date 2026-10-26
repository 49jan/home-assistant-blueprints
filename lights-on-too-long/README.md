# Luči predolgo prižgane (Lights On Too Long)

Home Assistant blueprint, ki na telefon pošlje obvestilo, ko katera od spremljanih luči ali stikal (`light`, `switch`) gori predolgo, in ponudi ugasnitev vseh ali samo posameznih.

## Datoteke

- [`lights-on-too-long.yaml`](lights-on-too-long.yaml) – blueprint za avtomatizacijo.
- [`tests/`](tests) – offline testi (struktura, `!input` reference, Jinja predloge, izračun zamujenih luči).

## Delovanje

1. Ko je spremljana entiteta prižgana dlje od nastavljenega časa (privzeto 1 h), pride **eno zbirno obvestilo** z imeni luči in gumbi:
   - **Ugasni vse** – ugasne vse zamujene luči,
   - **Ugasni posamezno** – za vsako luč pošlje svoje obvestilo z gumbom **Ugasni**,
   - **Pusti** – zapre obvestilo.
2. Opomnik se pošlje samo enkrat na prižig (brez ponavljanja).
3. Neobvezno: če so **vse izbrane osebe (`person`) odsotne**, vpraša že po krajšem času (privzeto 5 min po odhodu zadnje osebe ali po prižigu, če so že vsi odsotni).
4. Izključene entitete se ne spremljajo in jih gumbi nikoli ne ugasnejo.
5. Neobvezno tiho obdobje (npr. 23:00–07:00) prepreči obvestila doma; ob odsotnosti vseh ne velja.

Odziv na gumbe je v isti avtomatizaciji (event `mobile_app_notification_action`), zato deluje tudi po restartu Home Assistanta. ID-ji gumbov vsebujejo entiteto avtomatizacije, zato se več instanc blueprinta ne meša.

## Zahteve

- Home Assistant 2024.10.0 ali novejši.
- Aplikacija Home Assistant Companion in storitev `notify.mobile_app_*`; ime storitve vpišeš v vhod *Notify storitve telefonov*.

## Omejitve

- Časovnik "prižgano dlje kot" se po restartu Home Assistanta ponastavi; luči, prižgane pred restartom, sprožijo opomnik šele ob naslednjem prižigu.
- Android prikaže največ 3 gumbe, zato je posamezna izbira dvostopenjska (*Ugasni posamezno*).
- Če izbereš osebe in pritisneš gumb po njihovem prihodu domov, se za ugasnitev uporabi krajši prag (čas odsotnosti), da gumb ugasne tisto, kar je bilo navedeno v obvestilu.

## Testi

```powershell
.venv\Scripts\python -m unittest discover -s lights-on-too-long\tests -v
```
