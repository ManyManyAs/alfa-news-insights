# Проверка RSS-кандидатов

Прогон: 2026-10-24T01:15 UTC

| Источник | Адрес | Ответ | Новостей | Свежая | Доля про экономику | Пример |
|---|---|---|---|---|---|---|
| Минфин России | https://minfin.gov.ru/ru/press-center/?rss=Y | ошибка | — | — | — | ParseError: mismatched tag: line 58, column 2 |
| Минфин России | https://minfin.gov.ru/ru/rss/ | ошибка | — | — | — | HTTPStatusError: Client error '404 Not Found' for url 'https://minfin.gov.ru/ru/rss/'
For more information check: https://developer.mozil |
| Минэкономразвития | https://www.economy.gov.ru/material/news/rss/ | ошибка | — | — | — | ConnectTimeout: timed out |
| Минэкономразвития | https://economy.gov.ru/rss/ | ошибка | — | — | — | ConnectTimeout: timed out |
| ФНС России | https://www.nalog.gov.ru/rn77/about_fts/fts/rss/ | ошибка | — | — | — | ParseError: not well-formed (invalid token): line 59, column 71 |
| ФНС России | https://www.nalog.gov.ru/rss/rn77/news/ | ошибка | — | — | — | ParseError: not well-formed (invalid token): line 59, column 71 |
| Росстат | https://rosstat.gov.ru/rss | ошибка | — | — | — | ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1010) |
| Госдума | http://duma.gov.ru/news/rss/ | ошибка | — | — | — | HTTPStatusError: Client error '404 Not Found' for url 'http://duma.gov.ru/news/rss/'
For more information check: https://developer.mozill |
| Парламентская газета | https://www.pnp.ru/rss/index.xml | ✓ | 50 | 2026-10-24T01:00 | 0.38 | Путин поручил расширить применение технологии восстановления тканей при лечении ранений |
| КонсультантПлюс — горячие документы | https://www.consultant.ru/rss/hotdocs.xml | ошибка | — | — | — | ReadTimeout: The read operation timed out |
| Гарант — новости законодательства | https://www.garant.ru/rss/ | ошибка | — | — | — | HTTPStatusError: Client error '403 Forbidden' for url 'https://www.garant.ru/rss/'
For more information check: https://developer.mozilla. |
| Интерфакс — бизнес | https://www.interfax.ru/rss/business | ошибка | — | — | — | HTTPStatusError: Client error '404 Not Found' for url 'https://www.interfax.ru/rss/business'
For more information check: https://develope |
| Ведомости — экономика | https://www.vedomosti.ru/rss/rubric/economics | ✓ | 200 | 2026-10-23T21:57 | 0.69 | ФТС перевыполнила годовой план сборов за девять месяцев |
| Ведомости — бизнес | https://www.vedomosti.ru/rss/rubric/business | ✓ | 200 | 2026-10-23T21:45 | 0.39 | Самый северный юг российского виноделия делает ставку на игристое |
| Коммерсантъ — бизнес | https://www.kommersant.ru/RSS/section-business.xml | ✓ | 79 | 2026-10-24T00:25 | 0.72 | Новак сообщил об отправке первых танкеров с российским дизелем в сторону США |
| Коммерсантъ — финансы | https://www.kommersant.ru/RSS/section-finance.xml | ✓ | 26 | 2026-10-23T22:22 | 0.85 | Золото впервые с конца июля упало ниже $4000 за унцию |
| Forbes Россия | https://www.forbes.ru/newrss.xml | ошибка | — | — | — | ParseError: not well-formed (invalid token): line 1, column 13 |
| CNews — ИТ-рынок | https://www.cnews.ru/inc/rss/news.xml | ✓ | 200 | 2026-10-23T17:57 | 0.58 | «Нокстюрн» начал выявлять критическую уязвимость PHP в день ее раскрытия |
| Retail.ru | https://www.retail.ru/rss/news/ | старое/пусто | 0 | — | 0 |  |
| Агроинвестор | https://www.agroinvestor.ru/rss/ | ошибка | — | — | — | ParseError: not well-formed (invalid token): line 12, column 62 |
| Frank Media | https://frankmedia.ru/feed | ✓ | 60 | 2026-10-23T17:48 | 0.98 | ЦБ не может выйти из дня сурка: что значат решения опорного заседания регулятора |
| Экономика и жизнь | https://www.eg-online.ru/rss/ | ошибка | — | — | — | HTTPStatusError: Client error '404 Not Found' for url 'https://www.eg-online.ru/rss/'
For more information check: https://developer.mozil |
