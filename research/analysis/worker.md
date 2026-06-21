
### Translated Text

### Transkriptsiooni jätk: Koodi süvavaade ja tõrgete esilekutsumine (Uuendatud vastavalt tegelikule arhitektuurile)

**[4:00–4:30] Ekraanil: `app/services/outbox_service.py` (read 25–45).**

"Teooria on ilus, aga vaatame koodi. Siin, failis `outbox_service.py`, on meie andmebaasitransaktsiooni süda. Pöörake tähelepanu ridadele 38 kuni 42. Me lisame nii allalaadimise töö kui ka väljastuskasti kirje samasse sessiooni ja käivitame ühe `db.commit()` käsu. See ongi meie atomaarsuse garantii – kui andmebaas peaks sel hetkel ootamatult seiskuma, ei teki meil kunagi n-ö kummitustöid, mida keegi ei töötle."

**[4:30–5:00] Ekraanil: `worker/processor.py` (`sync_outbox_to_queue` meetod ja atomaarne hõivamine).**

"Edasi vaatame edastusteenust failis `processor.py`. Siin on meie versiooni 5.1 korrektsioon. Nagu näete, kasutame päringus `with_for_update(skip_locked=True)`. See on kõrgtaseme detail, mis lubab meil käivitada kasvõi kümme edastusteenust korraga ilma lukustuskonfliktideta.

Ja siin, real 145, on meie hulgikustutamise loogika. Me ei raiska aega ega andmebaasi ressursse oleku uuendamisele – kui sõnum on Redises, on väljastuskasti kirje oma töö teinud ja me eemaldame selle koheselt.

Sama põhimõte kehtib töö hõivamisel ehk `claim_job` meetodis. Me ei kasuta keerulisi lukke, vaid lihtsat SQL-i `WHERE status = 'pending'` tingimust. See on puhas, kiire ja laseb andmebaasi MVCC-mootoril kogu raske töö ära teha."

**[5:00–5:20] Ekraanil: `worker/main.py` (Sujuva seiskamise käsitleja).**

"Lõpetuseks koodi poolelt – sujuv seiskamine. Failis `main.py` näete, et oleme loobunud varasematest lihtsatest olekumuutujatest ja jäikadest ajapiirangutest. Seiskamise juhtimiseks kasutame nüüd sündmuspõhist arhitektuuri ehk `asyncio.Event` objekti nimega `shutdown_event`. Enne iga uue töö hõivamist arvutame `get_grace_period_remaining()` funktsiooniga allesjäänud ajapuhvri. Seda sama väärtust kasutame ka Redise `BRPOP` käsu dünaamilise aegumislimiidina.

Kui ajapuhver saab nulli, väljub süsteem tsüklist puhtalt ega võta enam uusi töid vastu, vaid lihtsalt lõpetab töö. Me ei suru käimasolevaid töid siin enam 25-sekundilise jäiga piiranguga kinni. Juhul kui seiskamissignaal tabab meid otse allalaadimise keskel, püüab asünkroonse tühistamiserindi kinni `processor.py`. Just seal asub meie loogika poolelijäänud ülesande andmebaasi tagasi panemiseks ja `_requeue_job` meetodi väljakutsumiseks. See muudab süsteemi tunduvalt tõhusamaks ja Kubernetese elutsükliga loomulikumalt ühilduvaks."

**[5:20–6:00] Ekraanil: Otseesitlus (Juhtpaneel ja terminal kõrvuti). Algab tõrgete esilekutsumine.**

"Kõik see tundub paberil ilus, aga süsteemi tegelik väärtus selgub kriisiolukorras. Teeme läbi ühe eksperimendi.

Ekraani vasakul pool on meie HTMX-il põhinev kasutajaliides, kus käib parajasti video allalaadimine. Paremal pool on minu terminal. Ma kavatsen nüüd keset protsessi **Redise konteineri jõuga peatada**.

*[Redise peatamine]*

Pange tähele, mis juhtub. Meie SSE-voog ehk olekuuuendused ei katke, vaid süsteem lülitub automaatselt ümber küsitlusrežiimile. Rakendusliides märkab, et Redis on kättesaamatu, ja hakkab uusi tellimusi koguma andmebaasi väljastuskasti tabelisse. Töötlusprotsess aga jätkab oma käimasolevat ülesannet, sest see on juba mälus.

Nüüd käivitan ma Redise uuesti.

*[Redis käivitub]*

Vaadake logisid: edastusteenus märkab koheselt ühenduse taastumist, tühjendab väljastuskasti tabeli ja saadab ootel olnud tööd järjekorda. Kasutaja jaoks oli see vaid väike viivitus olekuribal, aga süsteemi jaoks oli see edukalt läbitud katastroofistsenaarium. See ongi Vooglaadija tegelik tugevus – me ei karda katkestusi."

---

### Miks see osa on 5/5 tase? (Ajakohastatud)

1. **Koodi ja loogika seos:** Te ei loe lihtsalt koodi ette, vaid selgitate, *miks* see rida on oluline (nt lukustuskonfliktide vältimine).
1. **Vigade tunnistamine ja parandamine:** Mainides "versiooni 5.1 korrektsiooni" (kustutamine vs. uuendamine), näitate, et olete projekti käigus teinud süvaanalüüsi ja optimeerinud jõudlust.
1. **Tõrkekülv:** Redise peatamine esitluse ajal on väga enesekindel lüke. See tõestab, et teie arhitektuursed lubadused (väljastuskasti muster) reaalselt toimivad.
1. **Sündmuspõhine seiskamine:** Selgitus dünaamilise ajapuhvri ja asünkroonsete sündmuste (`asyncio.Event`) kohta näitab sügavat arusaama Pythoni asünkroonsest programmeerimisest. Te ei tugine toorele jõule ega "häkkidele", vaid jagate kohustused (seiskamise jälgimine vs. töö tagasi järjekorda panemine) elegantselt erinevate failide ja komponentide vahel.

### Terminology Notes

* **Sündmuspõhine arhitektuur (ingl *event-driven architecture*):** Parim vaste lahendusele, mis tugineb koodisündmustele (näiteks `asyncio.Event`), mitte püsivale küsitlemisele või jäikadele ajapiirangutele.
* **Ajapuhver (ingl *grace period*):** Eestikeelne IT-termin aja kohta, mis antakse süsteemile või protsessile kontrollitud seiskumiseks või taastumiseks (asendab laenatud "grace period" ja pikemat "sujuva seiskamise periood").
* **Dünaamiline aegumislimiit (ingl *dynamic timeout*):** Oskussõna ajapiirangule, mis arvutatakse reaalajas (nagu koodis tehtav `get_grace_period_remaining()`), mitte ei ole eelnevalt püsivalt koodi sisse kirjutatud (kõvakodeeritud).
* **Asünkroonne tühistamiserind (ingl *asyncio.CancelledError*):** Asünkroonse programmeerimise erind (*error/exception*), mis annab märku käimasoleva tegevuse katkestamise vajadusest. Eesti keeles eelistatakse veateate asemel kasutada sõna "erind", kuna tegemist on kinni püütava erandolukorraga.
* **Olekumuutuja (ingl *boolean flag / state variable*):** Kirjeldab koodis kasutatavat lihtsat muutujat (nagu varasem `self.is_polling`), mida uuendatakse sündmuste juhtimiseks.
