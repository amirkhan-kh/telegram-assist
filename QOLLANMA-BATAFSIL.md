# 📕 Joni — BATAFSIL Texnik Qo'llanma (har bir detal)

Bu hujjat **har bir funksiya, har bir so'rov, har bir tugma va validatsiyani**
sinchiklab tushuntiradi: *nima deb yozasiz → Joni nimani tekshiradi → qanday
event bajariladi → qanday javob qaytadi → qaysi tugmalar chiqadi*.

> Sodda qo'llanma uchun: **QOLLANMA.md**. Bu fayl — to'liq spravochnik.

---

## 📑 Mundarija
- [0. Umumiy qoidalar (hammasiga taalluqli)](#g0)
- [1. Pastki tugmalar (reply menu) — to'liq jadval](#g1)
- [2. ⏰ Eslatma (bir martalik)](#g2)
- [3. 🔁 Takroriy eslatma](#g3)
- [4. 🤝 Va'da](#g4)
- [5. ✅ Topshiriq (boshqa odamga, nazorat bilan)](#g5)
- [6. 📅 Uchrashuv](#g6)
- [7. ✉️ Xabar yuborish (hozir / keyin)](#g7)
- [8. 📆 Muhim sana qo'shish](#g8)
- [9. 🪪 Ma'lumotlarim (pasport / mashina)](#g9)
- [10. 📓 Qaror yozish](#g10)
- [11. 💰 Qarz qayd qilish](#g11)
- [12. 🔎 Ko'rish/ro'yxat so'rovlari](#g12)
- [13. 📅 Kalendar / 📧 Gmail / 📝 Notion / 🕳 Bo'sh vaqt](#g13)
- [14. ❌ Bekor qilish](#g14)
- [15. 🔘 Inline tugmalar va callback'lar — to'liq](#g15)
- [16. 🌙 Kun yakuni — qadamma-qadam](#g16)
- [17. 🌅 Ertalabki reja + tasdiqlash darvozasi](#g17)
- [18. ⏳ Vaqt iboralari — to'liq jadval](#g18)
- [19. 🧪 Validatsiya va xato xabarlari](#g19)
- [20. 💻 Skriptlar (terminal buyruqlari)](#g20)

---

<a name="g0"></a>
## 0. Umumiy qoidalar (hammasiga taalluqli)

1. **Faqat egasi.** Bot faqat egasining (OWNER) Telegram chatidan buyruq qabul
   qiladi. Boshqa odam yozsa — javob bermaydi.
2. **Til.** Hammasi o'zbekcha (lotin). Siz oddiy gap bilan yozasiz, bot tushunadi.
3. **Yozma yoki ovozli.** Matn yoki ovozli xabar — ikkalasi ham ishlaydi
   (ovoz avtomatik matnga o'giriladi va sizga «🎙 «...»» ko'rinishida ko'rsatiladi).
4. **Har bir buyruq oqimi:** sizning gapingiz → *⏳ Bajarilmoqda…* → natija
   (xabar tahrirlanadi). Xato bo'lsa — aniq sabab yoziladi.
5. **Ertalabki tasdiqlash darvozasi.** Agar 06:00 dagi kunlik reja
   tasdiqlanmagan bo'lsa, bot **hech qanday buyruqni bajarmaydi** — avval
   «✅ Tasdiqlash» bosiladi (17-bo'limga qarang).
6. **NLU «miya».** Gapni Google Gemini (Vertex) tushunadi. Tushunmasa
   «Tushunmadim, qaytaroq ayting» deydi.

---

<a name="g1"></a>
## 1. Pastki tugmalar (reply menu) — to'liq jadval

Yozish maydoni ostidagi doimiy tugmalar. Bosilganda darrov quyidagi event ishlaydi:

| Tugma (label) | Bajariladigan event | Natija |
|---------------|---------------------|--------|
| 📋 **Bugungi reja** | `list_agenda(scope=today)` | Bugungi eslatma/va'da/topshiriq/uchrashuv |
| 🌙 **Kun yakuni** | `run_evening()` | Interaktiv checklist (16-bo'lim) |
| ⏰ **Menga eslat** | submenu ochadi | ➕ Yangi eslatma · 📋 Eslatmalarim |
| 📅 **Kalendar** | `show_calendar(scope=week)` | Google kalendar (bu hafta) |
| 📆 **Muhim sanalar** | `list_important_dates` | Saqlangan sanalar + «… kun qoldi» |
| 🪪 **Ma'lumotlarim** | submenu ochadi | 🪪 Pasport · 🚗 Texnik ko'rik · 🛡 Sug'urta · 📋 Saqlangan sanalar |
| 💰 **Qarzlar** | `list_finance(they_owe_me)` | Sizga qarzdorlar + jami summa |
| 📓 **Qarorlarim** | `list_decisions` | Qarorlar arxivi |
| 📰 **Yangiliklar** | `get_digest` | Kanallardan eng ommabop postlar |

> Tugma bosilganda NLU («miya») ishlatilmaydi — to'g'ridan-to'g'ri event ishlaydi
> (tezroq va bepul).

---

<a name="g2"></a>
## 2. ⏰ Eslatma (bir martalik) — `create_reminder`

**Qachon ishlaydi:** «esla», «eslat», «esimga sol», «menga … eslat».

**Namuna so'rovlar:**
> «10 daqiqadan keyin suv ichishni esla»
> «ertaga soat 10 da Nodirbekka qo'ng'iroq qilishni esla»
> «3 kundan keyin shartnomani imzolashni esla»

**Joni nimani aniqlaydi (maydonlar):**
- `text` — nimani eslatish (gapdan olinadi).
- `when` — vaqt (18-bo'limdagi jadval bo'yicha).
- `pre_alerts_minutes` — oldindan ogohlantirish (standart **15 daqiqa oldin**).

**Validatsiya:**
- Vaqt **aniq** bo'lishi shart. «soat 9» (faqat raqam, kun/ertalab-kechqurun
  aytilmasa) → *«Vaqt aniq emas: kun va 'ertalab/kechqurun'ni ayting»*.
- Vaqt umuman tushunilmasa → *«Vaqtni tushunolmadim…»*.

**Natija (tasdiq):**
> ⏰ Eslatma qo'yildi: {matn}
> 🕒 Vaqt: 23.06 09:00
>
> [ ❌ Bekor qilish ]

**Keyin nima bo'ladi:**
- 15 daqiqa oldin: *«⏰ Eslatma yaqinlashmoqda (23.06 09:00): {matn}»*.
- Vaqti kelganda: *«⏰ Eslatma vaqti keldi: {matn}»* +
  tugmalar: **[✅ Bajarildi] [⏰ 1 soatga] [⏰ Ertaga]**.
- Vaqti o'tib ketsa — «Rejalarim»dан avtomatik o'chadi (vaqtinchalik).

---

<a name="g3"></a>
## 3. 🔁 Takroriy eslatma — `create_reminder` (recurrence)

**Qachon:** «har kuni…», «har dushanba…», «har oy…», «oy oxirida…».

**Namuna:**
> «Har dushanba ertalab 8 da hisobotni esla»
> «Har kuni soat 9 da tabletka ichishni esla»
> «Oy oxirida ijara to'lovini esla»

**Joni aniqlaydi:** takror turi (kunlik/haftalik/oylik), hafta kuni yoki oy kuni,
soat/daqiqa. «oy oxirida» = oyning oxirgi kuni.

**Natija:**
> 🔁 Takroriy eslatma qo'yildi: {matn}
> 📆 Jadval: Har dushanba 08:00
> 🕒 Keyingi: 22.06 08:00
>
> [ ❌ Bekor qilish ]

**Otilganda:** *«🔁 Eslatma: {matn}»* + tugmalar **[✅ Bajarildi] [🚫 To'xtatish]**.
- **✅ Bajarildi** — shu martagi belgilanadi, takror davom etadi.
- **🚫 To'xtatish** — takrorni butunlay to'xtatadi.

---

<a name="g4"></a>
## 4. 🤝 Va'da — `create_promise`

**Qachon:** egasi O'ZI biror ishni qilishni va'da qilsa — «men … qilaman»,
«… yuboraman», «va'da berdim».

**Namuna:**
> «Ertaga soat 9 da hisobotni yuborishga va'da berdim»
> «Bugun kechqurun to'lovni amalga oshiraman»

**Maydonlar:** `what`, `deadline`, `counterparty_name` (ixtiyoriy — kimga),
`pre_alerts_minutes` (standart **30 va 10 daqiqa oldin**).

**Natija:**
> 🤝 Va'da yozib qo'yildi: {what}
> 🕒 Muddat: 21.06 09:00
>
> [ ❌ Bekor qilish ]

**Otilganda:** *«🤝 Va'dangiz yaqinlashmoqda…»* / *«🤝 Va'da vaqti keldi:…»* +
**[✅ Bajarildi] [⏰ 1 soatga] [⏰ Ertaga]**.

---

<a name="g5"></a>
## 5. ✅ Topshiriq (boshqa odamga, nazorat bilan) — `assign_task_with_followup`

**Qachon:** BOSHQA odam egasi uchun ish qilishi kerak — «Akmal … qiladi»,
«falonchidan … ol», «unga ayt qilsin», «… topshir, nazorat qil».

**Namuna:**
> «Aliga kechgacha hujjatni tayyorlashni top, nazorat qil»
> «Akmal ertaga hisobotni tayyorlaydi»

**Maydonlar:** `assignee_name`, `task`, `deadline`,
`pre_alert_to_owner_minutes` [15], `auto_followup_to_assignee` (ha/yo'q),
`followup_offsets_minutes` [-15, 0].

**Validatsiya / xatti-harakat:** agar bunday kontakt bo'lmasa — yengil kontakt
o'zi yaratiladi (ish kuzatilishi uchun).

**Natija:**
> ✅ Topshiriq nazoratga olindi: Ali — hujjatni tayyorlash
> 🕒 Muddat: 20.06 18:00
>
> [ ❌ Bekor qilish ]

**Keyin:** muddat oldidan egaga eslatma, deadline atrofida o'sha odamga
avtomatik eslatma (TEST rejimida sizga preview ko'rinadi).

---

<a name="g6"></a>
## 6. 📅 Uchrashuv — `schedule_meeting`

**Qachon:** «X bilan uchrashuv/meeting/meet qo'y/uyushtir», «X bilan gaplashaman»
(vaqt bilan).

**Namuna:**
> «Payshanba 15:00 da investor bilan uchrashuv qo'y»
> «Ertaga soat 11 da Akmal bilan meeting»

**Maydonlar:** `title`, `when`, `duration_minutes` (standart 30),
`invitee_names`, `create_meet_link` (standart ha), `notify_target_name`
(kim bilan — unga havola/eslatma boradi).

**Natija (Google ulangan):**
> 📅 Uchrashuv rejalashtirildi: Investor bilan uchrashuv
> 🕒 Vaqt: 25.06 15:00
> 🔗 Meet: https://meet.google.com/...
> ⏰ 1 kun va 1 soat oldin eslataman.
> 📨 Havola boshlanishida {ism}ga yuboriladi.
>
> [ ❌ Bekor qilish ]

**Google ulanmagan bo'lsa:** «(Google ulanmagani uchun Meet havolasi yaratilmadi.)»
— uchrashuv baribir saqlanadi va eslatmalar ishlaydi.

**Eslatmalar:** **1 kun oldin** va **1 soat oldin** (qisqa muddatli uchrashuvда
o'tib ketgani tushiriladi). Eslatma tugmalari: **[✅ Tushunarli] [📅 1 soatga]
[📅 Ertaga]** (ko'chirish).

---

<a name="g7"></a>
## 7. ✉️ Xabar yuborish — `send_message` (hozir) / `schedule_message` (keyin)

**Qachon:** «Akmalga … yubor», «opamga ayt», «… xabar ber». Vaqt aytilsa — keyinga.

**Namuna:**
> «Akmalga ovozli xabar yubor: ertaga kelaman»
> «Opamga rasmiy xabar yoz: hujjatlar tayyor»
> «Ertaga soat 9 da Karimga xabar yubor: yig'ilish bor»

**Maydonlar:** `recipient_name`, `content` (Joni tabiiy, muloyim matn yozadi),
`delivery` (**voice** standart / text), `formality` (neutral / **formal**),
`when` (faqat schedule_message).

**Validatsiya:**
- `content` bo'sh bo'lsa → *«Xabar matni bo'sh. Nima yuborishni ayting.»*.
- Kontakt **ism / @username / telefon** bo'yicha qidiriladi.
  - Topilmasa → *«"X" kontaktlarda topilmadi…»*.
  - Bir nechta bir xil ism → raqamli ro'yxat: *«Qaysi biri? Raqamini yozing»* →
    siz **1**, **2** deb javob berasiz.
  - Tanlangan kontaktда Telegram ID bo'lmasa → *«… uchun Telegram identifikatori
    yo'q…»*.

**Natija (hozir):** *«{ism}ga xabar yuborildi.»* (+ ovoz mavjud emas bo'lsa
ogohlantirish, + TEST rejimi eslatmasi).
**Natija (keyin):** *«✉️ {ism}ga xabar rejalashtirildi. 🕒 Vaqt: …»* + **[❌ Bekor]**.

> 🔒 **TEST rejimi** (`TEST_MODE=true`): uchinchi shaxsga xabar haqiqatan
> yuborilmaydi — sizga «preview» qilib ko'rsatiladi. Haqiqiy yuborish uchun
> `.env` da `TEST_MODE=false`.

---

<a name="g8"></a>
## 8. 📆 Muhim sana qo'shish — `add_important_date`

**Qachon:** tug'ilgan kun, oilaviy tadbir, to'lov sanasi va h.k. — oddiy gapда.

**Namuna:**
> «5-avgust Alining tug'ilgan kuni»
> «12-dekabr soliq to'lovi»

**Maydonlar:** `title`, `category` (birthday/document/payment/travel/health/other),
`month` (1-12), `day` (1-31), `year` (ixtiyoriy), `yearly` (har yili?),
`remind_days_before` (standart [1]).

**Validatsiya:** oy 1-12, kun 1-31 bo'lishi shart, aks holda
*«Sana noto'g'ri. Oy (1-12) va kunni (1-31) aniqroq ayting.»*.

**Natija:**
> 🎂 Muhim sana saqlandi: Alining tug'ilgan kuni
> 📅 Sana: 05.08 (har yili)
> 🔔 1 kun oldin eslataman.
>
> [ ❌ Bekor qilish ]

**Kategoriya belgilari:** 🎂 tug'ilgan kun · 📄 hujjat · 💳 to'lov · ✈️ safar ·
🩺 salomatlik · 📌 boshqa.

---

<a name="g9"></a>
## 9. 🪪 Ma'lumotlarim (pasport / mashina) — tugmali oqim

**🪪 Ma'lumotlarim** tugmasi → inline submenu:

| Tugma (callback) | So'raydi | Hisoblaydi | Eslatma |
|------------------|----------|------------|---------|
| 🪪 Pasport (`pd:passport`) | berilgan sana | +10 yil | 30, 7, 1 kun oldin |
| 🚗 Texnik ko'rik (`pd:inspection`) | oxirgi ko'rik | +1 yil (har yili) | 14, 3, 1 kun oldin |
| 🛡 Sug'urta (`pd:insurance`) | boshlangan sana | +1 yil (har yili) | 14, 3, 1 kun oldin |
| 📋 Saqlangan sanalar (`pd:list`) | — | — | Ro'yxatni ko'rsatadi |

**Oqim:**
1. «🪪 Pasport» bosasiz → Joni: *«Pasport berilgan sanasini yozing (15.03.2019)…»*.
2. Sanani **`KK.OO.YYYY`** ko'rinishida yozasiz (`.`, `/`, `-` yoki bo'sh joy ham bo'ladi).
3. Joni hisoblab saqlaydi:
   > ✅ Saqlandi: 🪪 Pasportni yangilash
   > 📅 Sana: 15.03.2029 (~3 yil)
   > 🔔 1, 7, 30 kun oldin eslataman.
   >
   > [ ❌ Bekor qilish ]

**Validatsiya:** sana noto'g'ri bo'lsa → *«📅 Sana noto'g'ri. KK.OO.YYYY
ko'rinishida yozing (masalan 15.03.2019).»* (qayta urinasiz).
Boshqa menyu tugmasini bossangiz — kiritish bekor bo'ladi.

---

<a name="g10"></a>
## 10. 📓 Qaror yozish — `log_decision`

**Qachon:** «bugun qaror qildim: …», «qaror qabul qildim», «shunday hal qildim».

**Namuna:**
> «Bugun qaror qildim: iyuldan yangi loyiha boshlaymiz»

**Validatsiya:** matn bo'sh bo'lsa → *«Qaror matni bo'sh. Qaroringizni ayting.»*.

**Natija:**
> 📓 Qaror jurnalga yozildi:
> «iyuldan yangi loyiha boshlaymiz»
>
> [ ❌ Bekor qilish ]

> Notion ulangan bo'lsa — qaror avtomatik **«Joni — Qarorlar»** bazasiga ham
> ko'chiriladi (Notion ishlamasa, lokal jurnal baribir saqlanadi).

---

<a name="g11"></a>
## 11. 💰 Qarz qayd qilish — `add_finance`

**Qachon:** qarz berish/olish.
> «Karimga 200 ming so'm qarz berdim» → **Karim sizga qarzdor** (credit).
> «Validan 50 ming oldim» / «men Valiga qarzdorman» → **siz qarzdor** (debt).

**Maydonlar:** `direction` (debt/credit), `counterparty_name`, `amount`,
`currency` (standart UZS), `due` (ixtiyoriy muddat), `note`.

**Validatsiya:**
- `amount` **musbat** bo'lishi shart. 0 yoki manfiy → *«Summa noto'g'ri.
  Iltimos, musbat miqdorni ayting…»*.
- Valyuta katta harfga o'tkaziladi (uzs → UZS).

**Natija:**
> 💰 Qarz yozib qo'yildi: Karim sizga 200 000 UZS qarzdor.
> 🕒 Muddat: 01.07 09:00   ← (agar muddat aytilgan bo'lsa)
>
> [ ❌ Bekor qilish ]

Muddat berilsa — o'sha kun eslatma keladi.

---

<a name="g12"></a>
## 12. 🔎 Ko'rish / ro'yxat so'rovlari (faqat o'qiydi)

| So'rov misoli | Event | Nima ko'rsatadi |
|---------------|-------|-----------------|
| «bugungi rejam», «rejalarim», «nima ishlarim bor» | `list_agenda` | ⚠️ Muddati o'tgan, ⏰ Eslatmalar, 🤝 Va'dalar, ✅ Topshiriqlar, 📅 Uchrashuvlar |
| «eslatmalarim», «eslatmalar ro'yxati» | `list_reminders` | Faol eslatmalar (⏰ bir martalik + 🔁 takroriy) |
| «uchrashuvlarim», «meetlarim» | `list_meetings` | Rejalashtirilgan uchrashuvlar (🔗 Meet) |
| «muhim sanalarim», «tug'ilgan kunlar» | `list_important_dates` | Sanalar + «… kun qoldi» |
| «qarorlarim», «qaror arxivi» | `list_decisions` | Qarorlar (sana bilan) |
| «kontaktlarim», «Akmalni top», «raqami 90… kim» | `list_contacts` | Ism + @username + 📞 telefon |
| «kim menga qarzdor», «men kimga qarzdorman» | `list_finance` | Qarzlar + jami summa |

**Eslatma:** `list_agenda` da **o'tib ketgan eslatmalar** ko'rinmaydi (vaqtinchalik).
Faqat **va'da / topshiriq** «Muddati o'tgan» bo'limida qoladi.

**Kontakt qidiruvi** quyidagilarni qo'llab-quvvatlaydi:
ism (kiril/lotin farqsiz), @username/nik, va **telefon raqami** (to'liq yoki
oxirgi raqamlari, masalan «1234567»).

---

<a name="g13"></a>
## 13. 📅 Kalendar / 📧 Gmail / 📝 Notion / 🕳 Bo'sh vaqt

**📅 Kalendar — `show_calendar`**
> «kalendarim», «bu haftagi kalendar», «bugungi kalendar»

Google kalendaringizni kunlar bo'yicha ko'rsatadi: soat (yoki «Kun bo'yi»),
voqea nomi, 🔗 Meet. Bo'sh kunlar tushiriladi. Ulanmagan bo'lsa —
*«📆 Google Calendar ulanmagan…»*.

**📧 Gmail — `list_emails`**
> «muhim xatlar», «emaillarim», «pochtamni tekshir»

O'qilmagan/muhim xatlar (⭐ — muhim belgisi). Ulanmagan bo'lsa ko'rsatma beradi.

**📝 Notion — `save_to_notion`**
> «Notion'ga saqla: Q3 strategiyasi», «Notion'ga yoz: …»

Matnni Notion «Eslatmalar» bazasiga yozadi. Bo'sh matn → rad etadi.

**🕳 Bo'sh vaqt — `find_free_slots`**
> «ertaga qaysi vaqtlarim bo'sh», «bu hafta bo'sh vaqtlarim»

Ish soatlari (standart 09:00–18:00) ichidagi bo'sh oraliqlarni ko'rsatadi
(Google kerak).

---

<a name="g14"></a>
## 14. ❌ Bekor qilish — `cancel_item`

**Qachon:** «… bekor qil». Yangi qo'shilgan element ostidagi **[❌ Bekor qilish]**
tugmasi eng oson yo'l (ID kerak emas).

So'rov orqali: element turi + raqami kerak bo'lishi mumkin —
*«Bekor qilish uchun aniqroq ma'lumot kerak (masalan, elementning raqami).»*.

---

<a name="g15"></a>
## 15. 🔘 Inline tugmalar va callback'lar — to'liq

Joni yuborgan xabarlar ostidagi tugmalar va ular ortidagi amal:

### Eslatma / va'da / topshiriq otilganda
| Tugma | Callback | Amal |
|-------|----------|------|
| ✅ Bajarildi | `done:<kind>:<id>` | Bajarildi deb belgilaydi, jobni o'chiradi |
| ⏰ 1 soatga | `snz:<kind>:<id>:60` | 1 soatga kechiktiradi (qayta rejalaydi) |
| ⏰ Ertaga | `snz:<kind>:<id>:tmrw` | Ertaga 09:00 ga ko'chiradi |
| 🚫 To'xtatish | `stop:rem:<id>` | Takroriy eslatmani butunlay bekor qiladi |

`<kind>`: `rem`=eslatma, `prm`=va'da, `tsk`=topshiriq.

### Uchrashuv eslatmasida
| Tugma | Callback | Amal |
|-------|----------|------|
| ✅ Tushunarli | `ack:mtg:<id>` | Faqat tasdiqlaydi (o'zgartirmaydi) |
| 📅 1 soatga | `mv:mtg:<id>:60` | Uchrashuvni 1 soatga ko'chiradi |
| 📅 Ertaga | `mv:mtg:<id>:tmrw` | Ertaga shu vaqtga ko'chiradi |

### Yaratish tasdig'ida
| Tugma | Callback | Amal |
|-------|----------|------|
| ❌ Bekor qilish | `cancel:<kind>:<id>` | Endigina yaratilganni bekor qiladi/o'chiradi |

`cancel` uchun `<kind>`: rem/prm/tsk/mtg/msg(xabar)/fin(qarz)/evt(sana)/dec(qaror).

### Ertalabki reja
| Tugma | Callback | Amal |
|-------|----------|------|
| ✅ Tasdiqlash | `ok:brf:0` | Darvozani ochadi — bot ishlay boshlaydi |

### Submenu callback'lari
- **Menga eslat:** `mr:new` (qo'llanma ko'rsatadi), `mr:list` (eslatmalar ro'yxati).
- **Ma'lumotlarim:** `pd:passport`, `pd:inspection`, `pd:insurance`, `pd:list`.
- **Kun yakuni:** `eod:done:<kind>:<id>`, `eod:finish`, `eod:tmrw`, `eod:del`.

> Tugma bosilganda Joni qisqa bildirishnoma (toast) ko'rsatadi va xabarni
> yangilaydi (masalan «✅ Bajarildi» yozuvini qo'shib, tugmalarni olib tashlaydi).

---

<a name="g16"></a>
## 16. 🌙 Kun yakuni — qadamma-qadam

**Ishga tushishi:** «🌙 Kun yakuni» tugmasi **yoki** har kuni **21:00** avtomatik.

**1-qadam — checklist:**
> 🌙 Kun yakuni · 20.06.2026
> ✅ Bugun bajarilgan: 0 ta
> ⌛ Qolgan: 2 ta
> Bugun bajarganlaringizni belgilang 👇
> [ ⬜ Hujjatni yuborish ]
> [ ⬜ To'lovni amalga oshirish ]
> [ ✔️ Tugatdim ]

- **⬜ <ish>** bosilsa → o'sha ish **bajarildi** bo'ladi va ro'yxatdan chiqadi
  (ro'yxat jonli yangilanadi). Bajarilmaganiga tegmaysiz.
- Hammasi belgilansa → *«🎉 Barakalla — hammasi bajarildi!»*.

**2-qadam — «✔️ Tugatdim»:**
> ⌛ Bajarilmaganlar (1):
> • To'lovni amalga oshirish
> Ularni ertaga eslataymi?
> [ ✅ Ha, ertaga ]  [ 🗑 Yo'q, o'chir ]

- **✅ Ha, ertaga** (`eod:tmrw`) → bajarilmaganlar **ertangi rejaga** ko'chiriladi.
- **🗑 Yo'q, o'chir** (`eod:del`) → ular **butunlay o'chiriladi**.

---

<a name="g17"></a>
## 17. 🌅 Ertalabki reja + tasdiqlash darvozasi

**06:00 da avtomatik** chiqadi (sozlanadi). Tarkibi:
🌅 salom + sana · 📅 bugungi uchrashuvlar · ⏰ bugungi vazifalar ·
📞 muhim qo'ng'iroqlar · ⚠️ kechagi bajarilmaganlar · 🎂 yaqin muhim sanalar ·
📧 muhim xatlar (Gmail ulansa) · ⭐ **bugungi 3 ta prioritet**.

Post ostida **[✅ Tasdiqlash]** tugmasi.

**Darvoza qoidasi:**
- Tasdiqlanmaguncha siz **hech narsa qila olmaysiz** — har qanday yozuv yoki
  tugma: *«🌅 Avval bugungi rejani tasdiqlang…»*.
- **[✅ Tasdiqlash]** (`ok:brf:0`) bosilgach → *«✅ Reja tasdiqlandi — endi ish
  boshlasangiz bo'ladi»* va bot to'liq ishlaydi.
- Holat saqlanadi: bot qayta ishga tushsa ham, tasdiqlanmagan reja darvozani
  ochiq qoldirmaydi.

> «3 ta prioritet» evristika bilan tanlanadi: avval muddati o'tganlar → keyin
> uchrashuvlar → keyin eng yaqin muddatlilar.

---

<a name="g18"></a>
## 18. ⏳ Vaqt iboralari — to'liq jadval

| Siz aytasiz | Natija |
|-------------|--------|
| «10 daqiqada», «10 minutdan keyin» | hozir + 10 daqiqa |
| «yarim soatda» | hozir + 30 daqiqa |
| «2 soatda», «1 soat 30 minut» | nisbiy qo'shiladi |
| «3 kundan keyin» | 3 kundan keyin, soat 09:00 |
| «2 haftadan keyin» | 14 kundan keyin, 09:00 |
| «bugun», «ertaga», «indinga» | shu/kelasi kun (clock bo'lmasa 09:00) |
| «ertaga soat 9» | ertangi 09:00 |
| «soat 21:00», «15:30» | aniq soat (o'tib ketgan bo'lsa ertaga) |
| «3 kundan keyin soat 15 da» | 3 kun keyin 15:00 |
| «har dushanba [soat 8]» | takroriy, haftalik |
| «har kuni [soat 9]» | takroriy, kunlik |
| «oy oxirida», «har oy 15-kun» | takroriy, oylik |

**Aniq emas (rad etiladi):**
- «soat 9» (yolg'iz, 1–12, kun/ertalab-kechqurun yo'q) → kun yoki «21:00»
  ko'rinishini so'raydi.

---

<a name="g19"></a>
## 19. 🧪 Validatsiya va xato xabarlari

| Holat | Xabar |
|-------|-------|
| Vaqt aniq emas (yolg'iz «soat 9») | «Vaqt aniq emas: kun va 'ertalab/kechqurun'ni ayting…» |
| Vaqt umuman tushunilmadi | «Vaqtni tushunolmadim. Iltimos, qaytadan, aniqroq ayting…» |
| Qarz summasi ≤ 0 | «Summa noto'g'ri. Iltimos, musbat miqdorni ayting…» |
| Xabar matni bo'sh | «Xabar matni bo'sh. Nima yuborishni ayting.» |
| Qaror matni bo'sh | «Qaror matni bo'sh. Qaroringizni ayting.» |
| Sana formati noto'g'ri (pasport va h.k.) | «📅 Sana noto'g'ri. KK.OO.YYYY ko'rinishida yozing…» |
| Muhim sana oy/kun noto'g'ri | «Sana noto'g'ri. Oy (1-12) va kunni (1-31) aniqroq ayting.» |
| Kontakt topilmadi | «"X" kontaktlarda topilmadi…» |
| Bir nechta kontakt | «Qaysi biri? Raqamini yozing: 1. … 2. …» |
| Kontaktda Telegram ID yo'q | «… uchun Telegram identifikatori yo'q…» |
| Reja tasdiqlanmagan | «🌅 Avval bugungi rejani tasdiqlang…» |
| NLU tushunmadi | «Tushunmadim, qaytaroq ayting.» |
| AI limiti tugadi | «⏳ AI so'rovlar limiti…» (jonli sanoq bilan) |
| Google ruxsati tugagan | «Google ruxsati tugagan… python -m scripts.google_auth» |
| Notion/Gmail/Calendar ulanmagan | tegishli «… ulanmagan» ko'rsatmasi |

---

<a name="g20"></a>
## 20. 💻 Skriptlar (terminal buyruqlari — sozlash uchun)

Bular bir martalik sozlash uchun (server/dasturchi tomonda):

| Buyruq | Vazifasi |
|--------|----------|
| `python -m app.main` | Botni ishga tushirish |
| `python -m scripts.generate_session` | Userbot (Telethon) sessiyasini yaratish |
| `python -m scripts.google_auth` | Google (Calendar+Gmail+Meet) ruxsatini olish |
| `python -m scripts.clone_voice sample.mp3` | ElevenLabs ovoz klonini yaratish (ixtiyoriy) |
| `python -m scripts.seed_digest_demo` | Dayjest uchun namuna ma'lumot (sinov) |
| `python -m scripts.clear_digest_data` | Dayjest ma'lumotlarini tozalash |
| `docker compose up -d --build` | Serverda qurish va ishga tushirish |
| `docker compose logs -f app` | Loglarni kuzatish |
| `pytest` | Testlarni ishga tushirish |

**Asosiy sozlamalar (`.env`):**
- `OWNER_CHAT_ID` — botning egasi (Telegram raqamli ID).
- `MORNING_BRIEFING_HOUR=6`, `EVENING_REVIEW_HOUR=21` — reja/yakun vaqtlari.
- `DIGEST_DAILY_HOUR=-1` — yangiliklar avtomatik yuborilmaydi (faqat tugma bilan).
- `TEST_MODE` — true bo'lsa uchinchi shaxsga xabar yuborilmaydi (preview).
- `WORK_DAY_START_HOUR`/`WORK_DAY_END_HOUR` — bo'sh vaqt qidiruvi soatlari.
- `NOTION_API_KEY`, `NOTION_PARENT_PAGE_ID`, `GMAIL_MAX_RESULTS` — integratsiyalar.

---

### 🧭 Bir qarashda: «nima desam — nima bo'ladi»
> «esla» → ⏰ eslatma · «har dushanba esla» → 🔁 takroriy · «va'da berdim» → 🤝
> · «Akmal qiladi» → ✅ topshiriq · «uchrashuv qo'y» → 📅 + Meet · «… yubor» →
> ✉️ xabar · «5-avgust tug'ilgan kun» → 📆 sana · «qaror qildim» → 📓 ·
> «qarz berdim» → 💰 · «kalendarim» → 📅 · «muhim xatlar» → 📧 · «Notion'ga
> saqla» → 📝 · «rejalarim» → 📋 · «kun yakuni» → 🌙 checklist.

**Har bir amal tasdiq xabari bilan tugaydi va kerakli tugmalar bilan keladi —
shunchaki o'qing va bosing.** ✅
