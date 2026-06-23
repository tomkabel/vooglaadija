# Vooglaadija — TOP1 VÕIDU STSENAARIUM (SÜNTEES)

**Keel:** Eesti keel (native)  
**Kestus:** 2:45 (15s puhver 3-minutilise sloti sees)  
**Esineja:** Tom Kristian Abel  
**Stiil:** Karismaatiline, kuiv, raudselt kompetentne. Insener, kes on süsteeme lõhkunud ja nüüd ehitab neid, mis ei lagune.  

---

## LAVASTUS JA KAAMERA

### Dual-Screen Setup (KOHUSTUSLIK)

| Ekraan | Mida publik näeb | Mida NEMAD EI NÄE |
|--------|------------------|-------------------|
| **Projektor / Põhiekraan** | Grafana dashboard (4 paneeli) VÕI Vooglaadija UI | Mitte kunagi Chaos Lab nuppe |
| **Sinu sülearvuti** | — | Chaos Lab (`/web/chaos-lab`) — siin sa oled "mees kardina taga" |

Publik näeb AINULT tagajärgi. Mitte kunagi päästikut. See on *engineering theater*.

### Kriitilised Lavastusreeglid

- **Seisa projektsioonist vasakul või paremal**, mitte keskel. Sa ei varja ekraani. Sa raamid seda.
- **Chaos injection'i ajal — silmside žüriiga, MITTE ekraaniga.** Sa tead täpselt, mis juhtub. Sa ehitasid selle.
- **Kursori suurus: 150%.** Standardkursor on projektoril nähtamatu.
- **Brauseri suum: 100%.** Mitte 90%, mitte 110%. Dashboardi fondid on juba 48pt.

---

## HÄÄLEJUHISED

| Parameeter | Spetsifikatsioon |
|-----------|-----------------|
| Tempo | Rahulik, mõõdetud. Ära kiirusta — Grafana vajab 15 sekundit, et värv muuta. Sa tead seda. Ootamine on enesekindlus. |
| Toon | Kuiv, humoorikas, enesekindel. Mitte müüja. Mitte õpetaja. Kolleeg, kes on näinud asju lagunemas. |
| Pausid | Pärast iga rasket lauset. Pärast iga olulist numbrit. Grafana punasest roheliseks ülemineku ajal — VAIKUS. |
| Silmside | Kaamerasse/žüriisse: Hook, Taust, Close. Ekraanile: Demo ajal, aga mitte chaos injection'i klikil. |

### Keelatud

- ❌ "Aitäh" lõpus. Lõpeta "Vooglaadija." — punkt, vaikus, fade to black.
- ❌ Vabandamine. Mitte kunagi "loodetavasti", "proovime", "vabandust".
- ❌ Kiirustamine Grafana ülemineku ajal. Värvimuutus ON sõnum. Ära räägi sellest üle.
- ❌ Slängi ülekasutus. "Täiega", "megalt" jääb välja. "Trumbeldama" on lubatud — see on sihilik.

---

## STSENAARIUM

---

### 0:00–0:22 | HOOK: Lamborghini ja rehviveniil

**Visuaal:** Esineja täiskaadris. Projektoril tühi Grafana dashboard — kõik paneelid ROHELISED.  
**Esineja:** Rahulik, käed laual. Silmside.

---

> Mu isa töötab VAG-grupi autodiagnostikuna. Ühel päeval sõitis Möller Auto parklasse Lamborghini Urus.
>
> *[paus]*
>
> See auto oli teinud Euroopa-tuuri. Oslo — vahetati rattaid, ei aidanud. Kopenhaagen — vahetati pidureid, ei aidanud. Riia — vahetati terve roolisüsteem, ei aidanud.
>
> Probleem: maanteekiirusel hakkas auto vägivaldselt rappuma. Rool tahtis käest rebeneda. Tundus, nagu auto luud oleksid katki ja esirattad loperdaksid nagu murtud jäsemed.
>
> *[paus]*
>
> Mu isa kõndis auto juurde. Vaatas. Tõstis sõrme.
>
> "Rehvirõhu ventiil."
>
> *[mikropaus — kerge muie]*
>
> Läks lõunale.
>
> *[paus]*
>
> Neli rahvusvahelist meeskonda. Kolm kallist asendust. Probleem oli rehviveniilis.
>
> See on *cracked*. Mitte see, kes lisab keerukust. See, kes näeb läbi kõige selle ühe lihtsa punkti, kus süsteem tegelikult katki on.

---

**[0:22]**

---

### 0:22–0:52 | TAUST: Häkkerist arhitektiks

**Visuaal:** Esineja + taustal Grafana. Aeglane üleminek — Grafana täidab kaadrit.  
**Toon:** Rahulik, tõsine. Mitte hooplev. Mitte häbenev.

---

> See võime ei tulnud raamatust.
>
> Ma olen Tom. AI ja turbearhitekt. Aga minu teekond siia ei alanud ülikoolist. See algas pimedamast kohast. Kohast, kus ma õppisin süsteeme tundma neid rünnates.
>
> Eelmise aasta novembris luges Harju Maakohus mulle ette karistuse. Paragrahv 216¹. Phishing-tööriistade loomine ja levitamine. Evilginx. Man-in-the-Middle. Multi-Factor Authentication'ist mööda hiilimine. Ma ehitasin asju, mis võimaldasid teistel kontosid üle võtta. Ma müüsin neid krüpto eest. Ja ma läksin selle eest viieks kuuks reaalselt vangi.
>
> *[paus — pikem. Lase maanduda. Ära kiirusta edasi.]*
>
> Nüüd. Küsite: miks ma seda räägin?
>
> Räägin, sest see on kontekst. See "senior" mõtlemine — resilience, security-by-design — ei tulnud loengust. See tuli sellest, et ma olen näinud, kui õhukesed on süsteemide seinad. Kui kiiresti üks vigane sisend võib kogu toodangu maha võtta.
>
> Aga siin on pööre: nüüd ma kasutan seda teadmist, et ehitada süsteeme, mis EI lagune. See on nagu — kui sa oled piisavalt kaua lukke lahti murdnud, siis ühel hetkel saad aru, kuidas ehitada lukk, mida keegi lahti ei saa.
>
> *[paus — kergem toon]*
>
> Nüüd ma DDoSin omaenda servereid. Testin oma API'sid tuhandete päringutega paralleelselt. Kirjutasin oma booteri skripti. Suunasin oma serveri pihta. Sest kui mina seda ei tee — kes siis teeb? Mõni kutt foorumis, kes ei anna sulle 30-sekundilist hoiatust.
>
> See on *chaos engineering*. Sa lõhud oma süsteemi, kuni see muutub purustamatuks. Sa lähed trumbeldama — oma vastu.

---

**[0:52]**

---

### 0:52–1:15 | ÜLEMINEK: Vooglaadija ja MindTitan

**Visuaal:** Brauser — login leht Guest Demo nupuga.  
**Tegevus:** Klikk "Guest Demo" → automaatne redirect `/web/downloads`. 8 pre-seeded jobi nähtaval.

---

> Hiljuti kandideerisin ma MindTitanisse AI inseneriks. Mind ei testitud binaarpuu pööramises. Mind testiti *failure state'ide* peal.
>
> Robert küsis: "Mis juhtub, kui API annab juhuslikke 500-e ja 429-e ja sul on LLM agent käimas? Kuidas sa väldid, et naiivne AI ei lase kogu production-serverit põhja?"
>
> See on Vooglaadija. Minu vastus sellele küsimusele. Meedia allalaadimise API — YouTube, Vimeo, Twitch, TikTok, Dailymotion, Instagram. Kuus platvormi, üks liides.
>
> *[klikk Guest Demo — instant redirect]*
>
> Üks klikk. Null registreerimist. Kaheksa valmis jobi siin ees.
>
> *[scroll — näita erinevaid platvorme]*
>
> Aga see on lihtsalt CRUD-rakendus. Iga teine tiim siin saalis näitab teile täna oma *happy path'i*. See on tore, aga see on igav.
>
> Mina näitan teile, kuidas Vooglaadija ellu jääb, kui ma teda otse-eetris ründan.

---

**[1:15]**

---

### 1:15–1:50 | CHAOS INJECTION: LIVE

**EKRAANIVAHETUS:** Projektor → Grafana täisekraan. Sinu sülearvuti → Chaos Lab (publik EI NÄE).  
**Silmside:** Žürii poole, KUI vajutad esimest nuppu. See on power move.

---

> *[Projektor: Grafana, kõik ROHELINE. Sina: Chaos Lab taustal.]*
>
> See siin on meie Chaos Engineering Lab — arendustööriist, mille me ise ehitasime. Mitte simuleeritud video. Need nupud käivitavad päris toodangukoodi. Sama circuit breaker, sama zombie sweeper, sama retry chain, mis jookseb productionis.
>
> *[klikk — SIMULATE YOUTUBE 429. SILMSIDE ŽÜRIIGA, MITTE EKRAANIGA.]*
>
> Ma just simuleerisin YouTube rate-limiti. Neli-sada-kakskümmend-üheksa.
>
> *[osuta Grafana poole — Paneel 1 hakkab muutuma]*
>
> Vaadake Circuit Breaker State'i. See on hetkel roheline — CLOSED. Jälgige.
>
> *[15 sekundi ooteaeg. ÄRA RÄÄGI. Lase vaatajatel näha üleminekut ROHELINE → PUNANE.]*
>
> *[kui PUNANE on nähtaval]*
>
> Punane. OPEN. Circuit breaker avanes. Kõik päringud YouTube'ile lõpetatakse koheselt — ei mingit cascading failure'it, ei mingit krahhi. Süsteem degradeerub graatsiliselt. Iga järgmine request saab kohe 503 — ei oodata, ei proovita uuesti enne, kui olek muutub.

---

**[1:50]**

---

### 1:50–2:15 | ZOMBIE SWEEPER + RECOVERY

**Visuaal:** Grafana jätkuvalt täisekraanil.  
**Tegevus:** Teine klikk — SIMULATE WORKER CRASH.

---

> *[klikk — SIMULATE WORKER CRASH]*
>
> Nüüd tappisin ma worker'i keset tööd. Üks job jäi "processing" staatusesse — orvuks.
>
> *[osuta Paneel 2 — Queue Depth spike]*
>
> Queue depth hüppab. Job istub seal. Keegi ei töötle teda.
>
> *[osuta Paneel 4 — Recovery Events counter]*
>
> Aga — zombie sweeper. Meie stale-job reaper. Ta tuvastab orvu sekunditega. Counter tõuseb. Job on tagasi nõutud.
>
> *[5 sekundit VAIKUST — Grafana Paneel 1: PUNANE → KOLLANE → ROHELINE]*
>
> *[kui ROHELINE on tagasi]*
>
> Ja nüüd — automaatne taastumine. Circuit breaker sulgus. Queue tühjenes. Kasutaja ei näinud mitte midagi. Ei errorit. Ei "proovige hiljem uuesti". Süsteem lihtsalt... elas edasi.
>
> See ei ole *happy path*. See on *survival path*.

---

**[2:15]**

---

### 2:15–2:35 | NUMBRID JA AI

**Visuaal:** Split — brauser (job list, kõik "completed") + Grafana (kõik ROHELINE, Recovery Events counter kõrgem).  
**Tegevus:** Liigu brauseris jobide nimekirja ja Grafana vahel.

---

> Kuus platvormi. Kaksteist tuhat rida automatiseeritud teste. Kolmkümmend testmoodulit. Seitse teenust. Üks `docker compose up` käsk.
>
> AI throttle predictor — Redis sorted-set sliding window, mis arvutab YouTube'i rate-limiti riski ENNE kui esimene 429 üldse kasutajani jõuab. Native resilience. Mitte bolted-on AI.
>
> *[paus]*
>
> Aga tegelikult — see kõik ei ole point.

---

**[2:35]**

---

### 2:35–2:45 | CLOSE: CRACKED

**Visuaal:** Täiskaader esinejale. Grafana tagaplaanil — stabiilselt, rahulikult ROHELINE.  
**Silmside:** Kaamerasse / žüriisse. Mitte ekraanile.

---

> Point on siin.
>
> See süsteem sai ehitatud valusa protsessi kaudu. Ma alustasin lihtsast rakendusest — "tee API, lae video alla." Valmis.
>
> Aga siis tuli see osa, millest keegi ei räägi. Kõik need väsitavad, näiliselt võimatud enesele seatud nõuded. Circuit breaker. Transactional outbox. Zombie sweeper. Exponential backoff jitter'iga. Chaos injection API.
>
> Igaüks neist oli järjekordne mõra. Iga mõra oli õppetund. Mitte valik — nõue. Sest kui sa oled juba nii kaugele jõudnud, ei saa sa pooleli jätta. Sa pead edasi minema. Alati edasi.
>
> Iga takistus — üks crack.
>
> *[paus]*
>
> Seda tähendab olla *cracked*. Sa ei sünni selleks. Sa saad selleks — läbi selle, et sa lõhud oma süsteemi nii palju kordi, kuni enam ei ole midagi lõhkuda.
>
> *[pikk paus — silmside]*
>
> Robert küsis: kuidas ehitada LLM infrastruktuuri, mis ei lase AI agendil production-serverit maha võtta?
>
> Ma vastasin kohe. Sama stack. Sama circuit breaker. Sama outbox. Sama backoff.
>
> See ei olnud juhus. See oli Vooglaadija. Kaks kuud päris teed.
>
> *[viimane paus]*
>
> Me ehitasime selle, et ta jääks ellu seal, kus teised surevad.
>
> *[lõplik paus — 3 sekundit]*
>
> Vooglaadija.

---

**[2:45 — LÕPP]**

**Visuaal:** Hoia kaadrit 3 sekundit. Seejärel fade to black.  
**MITTE KUNAGI:** Ära ütle "aitäh". Ära põgene kaadrist. Lihtsalt seisa. Fade to black teeb ülejäänud töö.

---

## STRUKTUURIKAART: MIKS SEE TÖÖTAB

| Ajatelg | Sektsioon | "Cracked" roll | Emotsionaalne kaar |
|---------|----------|----------------|-------------------|
| 0:00 | Lamborghini | **Definitsioon:** Cracked = diagnoos, mitte keerukus | Intriig, huumor |
| 0:22 | Häkkeritaust | **Päritolu:** Cracked tuleb valest kohast, mitte loengust | Pinge, tõsidus |
| 0:35 | Vene booter | **Meetod:** Cracked = lõhud enda vastu | Pinge vabaneb — tume huumor |
| 0:52 | MindTitan | **Kontekst:** Miks see kõik on relevantne | Fookus, eesmärk |
| 1:15 | Chaos Live | **Tõestus:** Cracked filosoofia füüsilises vormis | Põnevus, "kas tõesti?" |
| 1:50 | Recovery | **Kinnitus:** Cracked süsteem terveneb ise | Rahuldus, "see tõesti töötab" |
| 2:15 | Numbrid + AI | **Skaala:** Cracked = 12K testi, 6 platvormi, AI prediction | Muljetavaldavus |
| 2:30 | "Iga takistus — üks crack" | **Definitsioon suletud:** Mantra, mis publiku kaasa võtab | Emotsionaalne kulminatsioon |
| 2:40 | Robert's challenge | **Tõestus suletud:** Cracked maksis ennast tagasi | Intellektuaalne rahuldus |
| 2:45 | "Vooglaadija." | **Vaikus:** Kõik on öeldud | Triumf |

---

## VARUVARIANT — KUI GRAAFANA EI TÖÖTA

> "See on tootesüsteem. Tootesüsteemid kukuvad kokku. See, mis teil praegu juhtub, on täpselt see probleem, mida Vooglaadija on projekteeritud lahendama. Circuit breaker on avanud. Queue depth on tõusnud. Zombie sweeper töötab taustal edasi. Ma lihtsalt ei saa seda teile praegu visuaalselt näidata — aga see töötab. Just nagu see süsteem on disainitud tegema. Annan teile hetke..."
>
> *[kui Grafana taastub]*
>
> "...ja nüüd — roheline. Süsteem tervenes. Nagu alati."

---

## MÄRKMED ESINEJALE

1. **Kanna midagi musta.** Lihtne, puhas. Mitte ülikond. Must T-särk või kampsun. Sa oled insener.

1. **Prindi see stsenaarium.** Aseta kaamera alla, silmade kõrgusele. Ära loe maha — see on turvavõrk.

1. **Joo vett enne.** Suu kuivab. Kõik teavad. Keegi ei valmistu.

1. **Kui eksid — jätka.** Mitte kunagi ära vabanda. Mitte kunagi ära alusta uuesti. Sa räägid asjast, mida tunned.

1. **Pärast "viieks kuuks vangi" — OOTA.** 3 sekundit. See lause PEAB maanduma. Kiirustamine = publiku kaotus.

1. **Grafana punasest roheliseks — VAIKUS.** Värvimuutus ON sõnum. Rääkimine selle üle nõrgestab seda.

1. **Chaos injection'i klikil — SILMSIDE ŽÜRIIGA.** Mitte ekraaniga. Sa tead, mis juhtub. Sa ehitasid selle. See on kogu point.

1. **"Vooglaadija." — ja siis SEISA.** Mitte "aitäh." Mitte põgenemine. Fade to black teeb ülejäänud töö.

---

*See stsenaarium on süntees kõigest, mis töötab — ja mitte midagi muud. Iga sõna on läbinud kriitika. Iga paus on põhjendatud. Iga üleminek kannab tähendust. See on nii hea, kui 3-minutiline tehniline ettekanne saab olla.*
