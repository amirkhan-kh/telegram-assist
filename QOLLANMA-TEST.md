# 🧪 Joni Assistant — To'liq test qo'llanmasi

Har bir funksiya/script uchun **topshiriqlar** (botga yuboriladigan matn/ovoz) va
**kutilgan natija**. Chuqur sinash uchun har bo'limda oddiy holat + nozik holatlar
berilgan. Botga (`@joni_assist_agent_bot`) egasi hisobingizdan yuboring.

> ⚠️ **Muhim eslatmalar (test rejimi):**
> - Hozir `TEST_MODE=true` — kontaktga yuborilgan har qanday xabar **haqiqiy odamga emas, o'zingizga** qaytadi (xavfsiz sinov). Rostdan yuborish uchun ayting — `TEST_MODE=false` qilaman.
> - **Notion** funksiyalari kalit yo'qligi uchun ishlamaydi (kerak bo'lsa ulaymiz).
> - Buyruqlarni **matn** yoki **ovoz** bilan yuborsangiz bo'ladi — ovozda ham xuddi shunday ishlaydi (bot avval «🎙 …» deb eshitganini ko'rsatadi).
> - Savol-javoblar `gemini-2.5-pro` (chuqur), buyruqlar `flash-lite` (~1-2s tez).

---

## 1. Boshlash va menyu (`bot/handlers.py`, `bot/ui.py`)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 1.1 | `/start` | Salomlashish + pastda tugmalar klaviaturasi |
| 1.2 | `/help` | Qisqa qo'llanma (misollar bilan) |
| 1.3 | «📋 Bugungi reja» tugmasi | Bugungi eslatma/vazifa/uchrashuvlar ro'yxati (yoki «bo'sh») |
| 1.4 | «📅 Kalendar» tugmasi | Bu haftalik Google kalendar |
| 1.5 | «📰 Yangiliklar» tugmasi | Kanal dayjesti (real postlar) |
| 1.6 | «⏰ Menga eslat» tugmasi | Eslatma submenyusi |
| 1.7 | «🪪 Ma'lumotlarim» tugmasi | Hujjatlar (pasport/sug'urta) submenyusi |
| 1.8 | «🌙 Kun yakuni» tugmasi | Bugungi bajarilmagan ishlar ro'yxati (interaktiv) |

---

## 2. Eslatmalar (`services/reminder_service.py` → `create_reminder`, `list_reminders`)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 2.1 | «10 daqiqadan keyin suv ichishni esla» | Eslatma qo'yiladi, 10 daqiqadan keyin keladi |
| 2.2 | «ertaga soat 9 da hisobotni yuborishni esla» | Ertaga 09:00 ga eslatma |
| 2.3 | «har dushanba ertalab 8 da yig'ilishni esla» | **Takroriy** (haftalik) eslatma |
| 2.4 | «har kuni 22:00 da tabletka ichishni esla» | Takroriy (kunlik) |
| 2.5 | «oy oxirida to'lovni esla» | Oy oxiri eslatmasi |
| 2.6 | «eslatmalarim» | Barcha faol eslatmalar ro'yxati |
| **Nozik** | «menga eslat soat 5 da Asadbekka qo'ng'iroq qilishni» | Bu **o'zingizga** eslatma (Asadbekka xabar EMAS) |

---

## 3. Va'da va topshiriq (`task_service.py` → `create_promise`, `assign_task_with_followup`)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 3.1 | «ertaga soat 9 da to'lovni amalga oshiraman» | Va'da (o'zingiz qilasiz) |
| 3.2 | «Aliga ayt kechgacha hujjatni tayyorlasin, nazorat qil» | **Topshiriq** — Ali bajaradi, sizga nazorat eslatmasi |
| **Nozik farq** | «Asadbekka eslat soat 5 da qo'ng'iroq qilsin» | send_message (Asadbekni ogohlantirish), eslatma EMAS |

---

## 4. Uchrashuv va kalendar (`meeting_service.py`, `google/calendar.py`)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 4.1 | «payshanba 15:00 da investor bilan uchrashuv qo'y» | Uchrashuv + 1 kun va 1 soat oldin eslatma |
| 4.2 | «ertaga soat 10 da Dilnoza bilan meeting qilaman» | Uchrashuv + Google Meet havolasi |
| 4.3 | «bu hafta bo'sh vaqtlarim qachon» | Bo'sh kalendar oynalari (`find_free_slots`) |
| 4.4 | «uchrashuvlarim» | Rejalashtirilgan uchrashuvlar |
| 4.5 | «kalendarim» / «bu haftagi kalendar» | Google kalendar ko'rinishi |

---

## 5. Xabar yuborish (`userbot/sender.py` → `send_message`, `schedule_message`)

> Userbot ulangan (kontaktlar sinxron). `TEST_MODE=true` — xabar sizga qaytadi.

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 5.1 | «Aliga xabar yubor: rahmat» | «🎙 Ovozli / 📝 Matn?» so'raydi → tanlaysiz → yuboriladi |
| 5.2 | «Aliga ovozli xabar yubor: rahmat» | To'g'ridan-to'g'ri ovozli yuboriladi (so'ramaydi) |
| 5.3 | «Aliga rasmiy xabar yubor: ertaga kelolmayman» | Rasmiy ohangda «siz» bilan yozilgan matn |
| 5.4 | «ertaga soat 9 da Aliga xabar yubor: yig'ilish bor» | **Rejalashtirilgan** xabar (ertaga 09:00 da) |
| 5.5 | «+998901234567 raqamiga xabar yubor: salom» | Raqamга to'g'ridan-to'g'ri |
| **Nozik (tuzatilgan)** | «El Nox bilan bog'lanib xabar yubor» → bot «kimga?» so'raydi → «elnox_uz» | Endi **yuborishни davom ettiradi** (arxiv qidiruv EMAS) |
| **Nozik** | Ismi bir nechta bo'lsa: «Akmalga xabar yubor» | Raqamlangan tanlash ro'yxati → birini tanlaysiz |

---

## 6. Moliya / qarzlar (`finance_service.py` → `add_finance`, `list_finance`)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 6.1 | «Akmalga 2 million qarz berdim» | Kredit (Akmal sizga qarzdor) |
| 6.2 | «Akmaldan 500 ming qarz oldim» | Qarz (siz Akmalga qarzdorsiz) |
| 6.3 | «Valiga 1-avgustgacha 300 ming berishim kerak» | Qarz + muddat |
| 6.4 | «kim menga qarzdor» | Sizga qarzdorlar ro'yxati + jami |
| 6.5 | «men kimga qarzdorman» | Siz qarzdor bo'lganlar |
| **Analitik** | «eng katta qarzim kimda» | `analyze_activity` — tahlil bilan javob |

---

## 7. Muhim sanalar va qarorlar (`event_service.py`, `decision_service.py`)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 7.1 | «5-avgust Alining tug'ilgan kuni» | Yillik sana + 1 kun oldin eslatma |
| 7.2 | «pasportim 12-dekabrda tugaydi» | Hujjat muddati + 7/3/1 kun oldin eslatma |
| 7.3 | «muhim sanalarim» | Saqlangan sanalar |
| 7.4 | «bugun qaror qildim: iyuldan yangi loyiha boshlaymiz» | Qaror arxivga saqlanadi |
| 7.5 | «qarorlarim» | Qarorlar arxivi |

---

## 8. Kontaktlar (`repositories/person_repo.py`, `contact_analysis_service.py`)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 8.1 | «kontaktlarim» | Kontaktlar ro'yxati (jami soni bilan) |
| 8.2 | «Akmal ismli kontaktlarim» | Nom bo'yicha qidiruv |
| **Analitik (NEW)** | «eng ko'p bir xil ismli kontaktlar qaysi, ularni ko'rsat» | Takrorlanuvchi ismlar + sonlari |
| **Analitik** | «bir xil ismli kontaktlar nechta, hammasini ro'yxat qil» | Aniq son + ro'yxat |
| **Analitik** | «username'i yo'q kontaktlar nechta» | Aniq statistika |
| **Follow-up** | (yuqoridan keyin) «eng kamlari-chi?» | Avvalgi savolni eslab davom etadi |

---

## 9. Chat/xabar tahlili (`telegram_chat_service.py`, `telegram_archive_service.py`, `chat_analysis_service.py`)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 9.1 | «Asadbek menga yuborgan oxirgi xabarni ko'rsat» | `get_chat_messages` — oxirgi xabar(lar) |
| 9.2 | «Bobur menga yuborgan rasmlarni tashla» | `search_chat_media` — rasmlar |
| 9.3 | «Akmal bilan oxirgi yozishmani qisqacha ayt» | `summarize_chat` — xulosa |
| 9.4 | «Do'kondagilar guruhida shahar ko'chasi tushgan videoni top» | `search_telegram_archive` — mos media |
| **Analitik (NEW)** | «kim bilan eng ko'p yozishaman» | Eng ko'p yozishilgan suhbatlar |
| **Analitik** | «eng faol chatlarim qaysi» | Eng ko'p xabarli chatlar |
| **Analitik** | «oxirgi hafta kim menga ko'p yozdi» | So'nggi 7 kun faolligi |

---

## 10. Ma'lumot xizmatlari (weather / news / digest / briefing / gmail)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 10.1 | «bugun ob-havo qanday» | `get_weather` — Toshkent ob-havosi (real) |
| 10.2 | «haftalik ob-havo Samarqandda» | Boshqa shahar + haftalik |
| 10.3 | «kun yangiliklari» | `get_news` — Daryo.uz sarlavhalari |
| 10.4 | «bugungi holatimni ayt» / «Jarvis bugun nimalar bor» | `jarvis_briefing` — ob-havo + reja |
| 10.5 | «emaillarim» / «o'qilmagan xatlar» | `list_emails` — Gmail o'qilmagan xatlar |

---

## 11. Savol-javob — umumiy suhbat (`answer_service.py` → `answer_question`, Pro)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 11.1 | «Yer quyoshdan qancha uzoqlikda» | Fakt (chuqur javob) |
| 11.2 | «1 dollar hozir O'zbekistonda necha so'm» | **Jonli** kurs (Google qidiruv bilan) |
| 11.3 | «menga samarali vaqtni boshqarish bo'yicha 3 ta maslahat ber» | Maslahatlar (Pro) |
| 11.4 | «buni inglizchaga tarjima qil: rahmat» | Tarjima |
| 11.5 | «salom, qalaysan» | Suhbat (quruq «Tushunmadim» EMAS) |
| **Follow-up** | «uning aholisi qancha» (avvalgi savol davomi) | Konteksni eslab javob beradi |

---

## 12. Ovoz (STT/TTS) (`services/voice_service.py`, `google_stt.py`)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 12.1 | Ovozli: «ertaga soat 10 da Aliga qo'ng'iroq esla» | Bot «🎙 …» deb eshitganini ko'rsatadi → eslatma qo'yadi |
| 12.2 | Ovozli **savol**: «bir dollar necha pul» | **Ovozли javob** qaytaradi (matnsiz) |
| 12.3 | Ovozli, shovqinli muhitda | Kichik xatolar bo'lsa ham asosiy ma'noni tushunadi |

---

## 13. Hujjat OCR (`document_service.py`, rasm orqali)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 13.1 | «🪪 Ma'lumotlarim» → «Pasport» → pasport **rasmini** yuboring | Muddatini o'qiydi, 7/3/1 kun oldin eslatma qo'yadi |
| 13.2 | «📸 Hujjat rasmlarim» | Saqlangan rasmni qaytaradi |
| 13.3 | Rasm o'qilmasa | Sanani qo'lda kiritishni so'raydi |

---

## 14. Reja/moliya umumiy tahlili (`activity_analysis_service.py` → `analyze_activity`)

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 14.1 | «ishlarimni umumiy tahlil qil» | Eslatma/uchrashuv/qarz/sana bo'yicha xulosa |
| 14.2 | «bu oyda nechta uchrashuvim bor» | Aniq son |
| 14.3 | «bajarilmagan vazifalarim qaysi» | Ro'yxat |
| **Bo'sh holat** | «eng katta qarzim kimda» (qarz yo'q bo'lsa) | «Qarz yo'q, qo'shsangiz javob beraman» — **insonday tushuntiradi** |

---

## 15. "Aql" xatti-harakati — kontekst va xatolarni tushuntirish

| # | Topshiriq | Kutilgan natija |
|---|-----------|-----------------|
| 15.1 | Tushunarsiz/g'alati matn yozing («asdfg qwerty») | Quruq «Tushunmadim» EMAS — insonday so'raydi yoki javob beradi |
| 15.2 | Bajarib bo'lmaydigan narsa so'rang | **Nega** bajarilmasligini aniq, samimiy tushuntiradi |
| 15.3 | Ketma-ket buyruq: «Aliga miting haqida ayt, ertaga 10 ga belgila» | Ikkala amalni ketma-ket bajaradi |

---

## 16. Inline tugmalar (dispatch → callbacks)

| # | Amal | Kutilgan natija |
|---|------|-----------------|
| 16.1 | Eslatma kelganda «✅ Bajarildi» | Belgilanadi, ro'yxatdan chiqadi |
| 16.2 | «⏰ Keyinga» (snooze) | Kunni/vaqtni tugmalar bilan tanlab kechiktiradi |
| 16.3 | Kontakt tanlash ro'yxatida raqam/tugma | O'sha kontakt bilan davom etadi |
| 16.4 | «🌙 Kun yakuni» → bajarilmaganlarni «Ertaga» yoki «O'chir» | Tanlovingizga ko'ra ertangi rejaga ko'chiradi yoki o'chiradi |

---

## 📋 Chuqur tekshiruv uslubi
Har bir funksiyani sinaganda quyidagilarga e'tibor bering:
1. **To'g'ri intent tanlandimi?** (masalan savol → javob, buyruq → amal)
2. **Tezlik** — buyruq ~1-2s, ovoz ~3-6s, savol (Pro) ~3-7s
3. **Aniqlik** — ismlar, sanalar, summalar to'g'ri o'qildimi
4. **Kontekst** — follow-up savol avvalgisini eslaydimi
5. **Xato holati** — bajarilmasa, sabab insonday tushuntirildimi

Xatolik yoki noto'g'ri xatti-harakat topsangiz — o'sha topshiriq + natijani menga yuboring, darhol tuzataman.
