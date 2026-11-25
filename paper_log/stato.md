# Registro di prova: previsioni di Claude contro il mercato

Aggiornato: 2026-11-25T19:45:02Z (UTC). Solo su carta, nessuna operazione reale o demo.
Catena di controllo del registro: integra.

## Pista A: eventi con probabilità di mercato (mercati di previsione)

- Casi chiusi che contano: 0. Aperti: 1. Annullati: 0. Chiusi ma esclusi dal punteggio (prezzo già visto): 0.
- Giudizio: troppo presto: 0 casi chiusi su 50 necessari.

| Id | Evento | Data | Claude | Mercato | Esito | Scommessa |
|---|---|---|---|---|---|---|
| A-0001 | Inflazione USA di novembre (annua) sopra 4,1% | 2026-12-10 | 54% | 2% | aperto |  |

## Pista B: direzione del prezzo dopo l'evento (contro il 50%)

- Casi chiusi che contano: 10. Aperti: 2. Annullati: 0. Chiusi ma esclusi dal punteggio (prezzo già visto): 0.
- Errore medio (Brier, più basso è meglio): Claude 0.266, mercato 0.250.
- Differenza a favore di Claude, intervallo al 90%: da -0.042 a 0.010 (se comprende lo zero, può essere caso).
- Scommesse su carta (solo quando lo scarto è di almeno 10 punti): 0, vinte 0, risultato netto 0.00 (punti percentuali, senza leva).
- Direzione indovinata: 4 su 10. Confronto: comprare sempre avrebbe reso -23.08 punti percentuali.
- Giudizio: troppo presto: 10 casi chiusi su 50 necessari.

| Id | Evento | Data | Claude | Mercato | Esito | Scommessa |
|---|---|---|---|---|---|---|
| B-0001 | Disney sale il giorno della trimestrale | 2026-11-17 | 45% | 50% | no (-1.48%) |  |
| B-0002 | Nvidia sale il giorno dopo la trimestrale | 2026-11-18 | 55% | 50% | no (-2.90%) |  |
| B-0003 | Target sale il giorno della trimestrale | 2026-11-18 | 43% | 50% | sì (+4.01%) |  |
| B-0004 | Walmart sale il giorno della trimestrale | 2026-11-19 | 55% | 50% | no (-1.04%) |  |
| B-0005 | Home Depot sale il giorno della trimestrale | 2026-11-17 | 47% | 50% | no (-2.45%) |  |
| B-0006 | Alibaba sale il giorno della trimestrale | 2026-11-19 | 53% | 50% | no (-1.26%) |  |
| B-0007 | Zoom sale il giorno dopo la trimestrale | 2026-11-24 | 56% | 50% | sì (+2.75%) |  |
| B-0008 | Analog Devices sale il giorno della trimestrale | 2026-11-24 | 52% | 50% | no (-17.37%) |  |
| B-0009 | Medtronic sale il giorno della trimestrale | 2026-11-24 | 47% | 50% | no (-4.56%) |  |
| B-0010 | Best Buy sale il giorno della trimestrale | 2026-11-24 | 42% | 50% | sì (+2.70%) |  |
| B-0011 | Dell sale il giorno dopo la trimestrale | 2026-11-25 | 48% | 50% | aperto |  |
| B-0012 | Deere sale il giorno della trimestrale | 2026-11-25 | 44% | 50% | aperto |  |

\* previsione fatta dopo aver già visto il prezzo di mercato: resta in elenco ma non entra nel punteggio.

Fine della prova: 2027-02-15, oppure prima se entrambe le piste arrivano a 50 casi chiusi.
