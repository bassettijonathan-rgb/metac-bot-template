# Regole del registro di prova (fissate il 15 novembre 2026, prima della prima previsione)

Scopo: capire, senza soldi veri, se le previsioni di Claude sugli eventi battono
quelle già contenute nei prezzi di mercato. Qui non si fa nessuna operazione,
né reale né demo. Solo se la prova riesce si parlerà di soldi veri, e la decisione spetta al titolare del progetto.

## Le due piste

**Pista A, eventi con una probabilità di mercato.** Domande sì/no con data certa
(decisioni di banche centrali, dati macro come inflazione e occupazione, esiti
regolatori) per cui esiste un mercato di previsione liquido (Kalshi, Polymarket).
Il prezzo del contratto è la probabilità del mercato. Su quei mercati qui non si opera:
servono solo come termine di confronto.

**Pista B, direzione del prezzo dopo l'evento.** È la pista più vicina a CFD,
knock-out e certificati. Domanda: "il titolo/indice chiuderà sopra la chiusura
precedente nella prima seduta che sconta l'evento?" (trimestrali di grandi
società, giorni di Fed/BCE, inflazione, occupazione). Qui il mercato non dà una
probabilità, quindi il termine di confronto è la moneta: 50%.

## Regole che rendono il registro affidabile

1. La previsione si scrive prima dell'evento. Lo script rifiuta eventi con data di oggi o passata.
2. Alla cieca: prima Claude fa la ricerca e fissa la sua probabilità, solo dopo
   legge il prezzo di mercato. Dopo averlo letto non si corregge più. Se il prezzo
   è già stato visto, la previsione va segnata `blind: false`, resta in elenco e
   non entra nel punteggio.
3. Una sola domanda per evento (per esempio una sola soglia per il dato
   sull'inflazione), scelta prima di vedere i prezzi: quella che per Claude è più
   vicina al 50%.
4. Pista A: solo mercati con scambi veri (volume di almeno 10.000 dollari sul
   contratto o scambi negli ultimi 3 giorni). Prezzo registrato: punto medio tra
   denaro e lettera.
5. Il registro (`registro.jsonl`) è a sola aggiunta. Ogni riga contiene l'impronta
   della riga precedente, quindi una modifica a posteriori si vede
   (`python3 registro.py verify`). Dopo ogni tornata il registro viene anche
   caricato su GitHub (ramo `claude/project-thread-do010u`, cartella `paper_log/`):
   la data del caricamento è una prova esterna che la previsione esisteva prima dell'esito.
6. Nessuna previsione viene cancellata o rifatta perché "sembra sbagliata".
   Evento annullato o rinviato oltre la fine della prova: esito `void`.

## Come si giudica (deciso prima, non si cambia)

- Misura principale: punteggio di Brier (errore quadratico medio, più basso è
  meglio) di Claude contro quello del mercato, sugli stessi casi.
- Scommesse su carta, solo quando lo scarto è ampio:
  - Pista A: se Claude e mercato distano almeno 10 punti, si "compra" il lato
    favorito da Claude al prezzo di mercato più 2 punti di costi.
  - Pista B: se Claude è almeno al 60% (o al massimo al 40%), si va lunghi (o
    corti) senza leva, con 0,15% di costi andata e ritorno. Confronto fisso con
    "comprare sempre".
- Giudizio per ciascuna pista, non prima di 50 casi chiusi:
  - **vantaggio credibile**: Brier migliore del mercato, intervallo al 90% della
    differenza tutto sopra lo zero, scommesse su carta in utile dopo i costi;
  - **indizio debole**: migliore e in utile, ma la differenza può essere caso;
  - **nessun vantaggio dimostrato**: tutti gli altri casi.
- Fine della prova: 15 febbraio 2027, oppure prima se entrambe le piste arrivano
  a 50 casi chiusi. Con 50-100 casi si vede solo un vantaggio grande: uno piccolo
  resterebbe invisibile, e in quel caso la risposta onesta è "non dimostrato".

## Procedura di ogni tornata (domenica e mercoledì sera)

1. `python3 registro.py due`: per ogni previsione scaduta cercare l'esito
   (pista A: esito ufficiale del dato o del mercato; pista B: chiusure giornaliere
   da Twelve Data, `--ret` = variazione percentuale chiusura su chiusura) e
   registrarlo con `resolve`.
2. Nuovi eventi dei prossimi 7-10 giorni, al massimo 8 previsioni per tornata:
   - calendario trimestrali (FMP `earnings-calendar`), solo società grandi e note;
   - eventi macro e banche centrali su Kalshi, per esempio
     `https://api.elections.kalshi.com/trade-api/v2/events?status=open&series_ticker=KXCPIYOY&with_nested_markets=true`
     (altre serie utili: KXFEDDECISION, KXPAYROLLS, KXU3/KXUE, KXGDP, KXPCEHEAD,
     KXISMSERVICES, KXCBDECISIONJAPAN, BOE, KXEZGDPYOYF; elenco completo:
     `/series?category=Economics`) e su Polymarket
     (`https://gamma-api.polymarket.com/events?closed=false&tag_slug=economy&order=volume&ascending=false`).
   - Quando si elencano gli eventi, chiedere esplicitamente di NON riportare i prezzi.
3. Ricerca prima di fissare il numero. Lista di controllo: regola esatta di
   risoluzione; ultimo dato ufficiale; dati correnti che entrano nel risultato
   (per esempio i prezzi della benzina del mese per l'inflazione); consenso degli
   analisti; frequenza storica; rifare i conti una seconda volta. Evitare le
   pagine che riportano quote di mercati di previsione o FedWatch.
4. Solo dopo, leggere i prezzi (`/markets?event_ticker=...` e, per controllare
   che siano vivi, `/markets/trades?ticker=...&limit=8`). Il lettore di pagine può
   sbagliare a trascrivere: se un numero è strano, rileggerlo da una seconda fonte.
5. Scrivere le previsioni in un file JSON e aggiungerle con
   `python3 registro.py add --file nuovo.json`, poi `python3 registro.py report`.
6. Copiare `registro.jsonl`, `stato.md`, `REGOLE.md`, `registro.py` nella cartella
   `paper_log/` del repository, commit e push sul ramo indicato sopra.
7. Al titolare si scrive solo: un riepilogo breve ogni due settimane (prima tornata
   di domenica dopo il 28 novembre, poi ogni 14 giorni), il giudizio finale, o un
   problema che blocca il registro. Le altre tornate sono silenziose.

## Limiti da tenere presenti

- Battere un mercato di previsione non significa guadagnare con un knock-out:
  tra la previsione giusta e il profitto ci sono spread, leva e il fatto che il
  prezzo può muoversi contro anche quando l'evento va come previsto. La pista B
  misura proprio questo passaggio, senza leva.
- Claude fa la ricerca sul web, quindi una cecità perfetta rispetto ai prezzi
  non è garantita: il consenso degli analisti è comunque pubblico.
- Casi della stessa settimana si muovono insieme (se il mercato crolla, crollano
  tutti i titoli): l'intervallo di confidenza è calcolato per evento, ma resta ottimistico.
