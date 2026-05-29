// Minimal RU/EN localization. No external dependency.
// Persists language preference (a non-sensitive UI preference) in localStorage.
// Notifies subscribers on language change so React components can re-render.

export type Lang = "ru" | "en";

const LS_KEY = "technicalAnalyst.language";
const DEFAULT_LANG: Lang = "ru";

type Dict = Record<string, string>;

const STRINGS: Record<Lang, Dict> = {
  ru: {
    // Drawer
    "drawer.assets": "Активы",
    "drawer.search.placeholder": "Поиск MOEX и BCS…",
    "drawer.search.bcsTokenRequired": "Для BCS GOODS требуется токен BCS",
    "drawer.add": "+ Добавить",
    "drawer.add.asset": "+ Добавить актив",
    "drawer.manage": "Управление",
    "drawer.done": "Готово",
    "drawer.cancel": "Отмена",
    "drawer.settings": "Настройки",
    "drawer.added": "Добавлено",
    "drawer.duplicate": "Уже в списке",
    "drawer.empty.title": "Список пуст",
    "drawer.empty.sub": "Добавьте первый инструмент в список",
    "drawer.remove": "Удалить",
    "drawer.drag.hint": "Перетащите, чтобы изменить порядок",

    // Settings — common
    "settings.title": "Настройки",
    "settings.tab.data": "Данные",
    "settings.tab.live": "Лента",
    "settings.tab.updates": "Обновления",
    "settings.tab.ai": "ИИ",
    "settings.tab.about": "О приложении",

    // Settings — data
    "settings.data.source": "Источник данных",
    "settings.data.token.paste": "Вставьте refresh-токен BCS",
    "settings.data.token.saved": "Токен BCS сохранён",
    "settings.data.token.source.label": "Источник токена",
    "settings.data.token.source.session": "пользовательский токен сессии",
    "settings.data.token.source.default": "встроенный токен сборки",
    "settings.data.token.source.none": "отсутствует",
    "settings.data.token.default.available": "В этой сборке доступен встроенный read-only токен BCS.",
    "settings.data.token.note.session": "Токен хранится только в памяти сессии — он теряется при перезапуске. Поддержка BCS экспериментальная.",
    "settings.data.token.note.moex": "Прямые данные MOEX. Авторизация не требуется.",
    "settings.data.token.warn": "Используйте только read-only токен BCS. Никогда не используйте токен с правами на торговлю.",
    "settings.data.fallback": "Переключаться на MOEX при сбое BCS",
    "settings.data.clear": "Очистить",
    "settings.data.test": "Проверить",
    "settings.data.testing": "Проверка…",
    "settings.data.test.ok": "Подключение OK",
    "settings.data.save": "Сохранить",
    "settings.data.placeholder.token": "Вставьте refresh-токен…",

    // Settings — live
    "settings.live.feed": "Лента в реальном времени",
    "settings.live.enable": "Включить обновления в реальном времени",
    "settings.live.status": "Статус",
    "settings.live.lastUpdate": "Последнее обновление",
    "settings.live.reconnect": "Переподключить",
    "settings.live.reconnecting": "Переподключение…",
    "settings.live.status.live": "В эфире",
    "settings.live.status.paused": "Приостановлено",
    "settings.live.status.stale": "Устаревшие данные",
    "settings.live.status.reconnecting": "Переподключение…",
    "settings.live.status.error": "Ошибка",
    "settings.live.note": "Интервалы опроса: котировки каждые 5 с, портфель каждые 15 с, сканер каждые 60 с. Интервалы увеличиваются автоматически, когда приложение в фоне.",

    // Settings — updates
    "settings.updates.current": "Текущая версия",
    "settings.updates.check": "Проверить обновление",
    "settings.updates.checking": "Проверка…",
    "settings.updates.upToDate": "У вас последняя версия",
    "settings.updates.available": "Доступно обновление",
    "settings.updates.unsupported": "Версия больше не поддерживается. Обновите приложение.",
    "settings.updates.download": "Скачать APK",
    "settings.updates.released": "Дата выпуска",
    "settings.updates.failed": "Не удалось проверить обновление",

    // Settings — AI
    "settings.ai.mode": "Режим панели ИИ",
    "settings.ai.mode.mock": "Демо",
    "settings.ai.mode.pa_short": "PA Short",
    "settings.ai.warning": "Режим PA Short не показал устойчивой прибыльности в бэктесте. Используется только для исследований.",
    "settings.ai.descriptions": "Описание режимов",
    "settings.ai.desc.mock": "Статичная демо-аналитика. Инференс модели не выполняется. Безопасно для UI-разработки и демонстраций.",
    "settings.ai.desc.pa_short": "Локальная модель price action, работает полностью на устройстве. Формирует краткие сводки по свечным паттернам. Данные не покидают устройство.",
    "settings.ai.note": "Анализ ИИ выполняется локально на устройстве. Данные не передаются внешним серверам.",

    // Settings — about
    "settings.about.app": "Technical Analyst",
    "settings.about.sources": "Источники данных",
    "settings.about.sources.moex": "MOEX — публичный API Московской биржи",
    "settings.about.sources.bcs": "BCS Broker — авторизованные рыночные данные (эксп.)",
    "settings.about.analysis": "ИИ и анализ",
    "settings.about.analysis.local": "Весь ИИ-инференс выполняется на устройстве",
    "settings.about.analysis.noexternal": "Данные анализа не передаются на внешние серверы",
    "settings.about.analysis.bundled": "Модели поставляются вместе с приложением",
    "settings.about.warn": "Приложение не даёт торговых рекомендаций, инвестиционных советов или сигналов. Все данные носят информационный характер. Торгуете на свой риск.",
    "settings.about.note": "Technical Analyst — независимый инструмент, не аффилирован с MOEX, BCS Broker или иной финансовой организацией.",
    "settings.about.language": "Язык",
    "settings.about.language.ru": "Русский",
    "settings.about.language.en": "English",

    // Chart
    "chart.live": "В ЭФИРЕ",
    "chart.paused": "ПАУЗА",
    "chart.stale": "УСТАРЕЛО",
    "chart.reconnecting": "ПЕРЕПОДКЛ.",
    "chart.error": "ОШИБКА",
    "chart.syncing": "СИНХР.",
    "chart.tab.info": "Инфо",
    "chart.tab.ai": "ИИ",
    "chart.tab.depth": "Стакан",
    "chart.tab.data": "Данные",
    "chart.tab.dataHide": "Скрыть",
    "chart.refresh": "Обновить",
    "chart.loading": "Загрузка",
    "chart.olderLoading": "Загрузка старых свечей…",
    "chart.olderNone": "Старых свечей нет",
    "chart.olderError": "Не удалось загрузить старые свечи",
    "chart.search.placeholder": "Поиск тикера…",
    "chart.search.bcsTokenRequired": "Для BCS GOODS требуется токен BCS",
    "chart.last": "Послед.",
    "chart.refreshTime": "Обновлено",
    "chart.refreshFailed": "Сбой обновления",
    "chart.candles": "свечей",
    "chart.nodata": "Нет свечей для",

    // Order book
    "ob.title": "Стакан",
    "ob.bid": "Bid",
    "ob.ask": "Ask",
    "ob.spread": "Спред",
    "ob.mid": "Среднее",
    "ob.depth": "Глубина",
    "ob.lastUpdate": "Обновлено",
    "ob.tokenRequired": "Для стакана требуется токен BCS",
    "ob.loading": "Загрузка…",
    "ob.selectInstrument": "Выберите инструмент для просмотра стакана.",
    "ob.noData": "Нет данных.",

    // AI panel
    "ai.experimental": "Экспериментальный ИИ",
    "ai.signal": "Сигнал ИИ",
    "ai.short.risk": "Риск SHORT",
    "ai.research.only": "Только для исследований",
    "ai.research.warning": "Только для исследований. Бэктест не показал положительного матожидания.",
    "ai.calculating": "Расчёт…",
    "ai.notrade": "НЕ ТОРГОВАТЬ",
    "ai.confidence.high": "Высокая уверенность",
    "ai.confidence.medium": "Средняя уверенность",
    "ai.confidence.low": "Низкая уверенность",
    "ai.horizon": "Горизонт",
    "ai.model": "Модель",
    "ai.candles": "свечей",
    "ai.disclaimer": "Экспериментальный локальный сигнал. Не является инвест. советом.",
    "settings.data.token.client.label": "Клиент",
    "settings.data.token.client.auto": "auto",
    "settings.data.token.client.read": "trade-api-read",
    "settings.data.token.client.write": "trade-api-write",
  },
  en: {
    // Drawer
    "drawer.assets": "Assets",
    "drawer.search.placeholder": "Search MOEX and BCS…",
    "drawer.search.bcsTokenRequired": "BCS token required for GOODS",
    "drawer.add": "+ Add",
    "drawer.add.asset": "+ Add asset",
    "drawer.manage": "Manage",
    "drawer.done": "Done",
    "drawer.cancel": "Cancel",
    "drawer.settings": "Settings",
    "drawer.added": "Added",
    "drawer.duplicate": "Already in watchlist",
    "drawer.empty.title": "No assets yet",
    "drawer.empty.sub": "Add your first instrument to the watchlist",
    "drawer.remove": "Remove",
    "drawer.drag.hint": "Drag to reorder",

    // Settings — common
    "settings.title": "Settings",
    "settings.tab.data": "Data",
    "settings.tab.live": "Live",
    "settings.tab.updates": "Updates",
    "settings.tab.ai": "AI",
    "settings.tab.about": "About",

    // Settings — data
    "settings.data.source": "Data Source",
    "settings.data.token.paste": "Paste BCS refresh token",
    "settings.data.token.saved": "BCS token saved",
    "settings.data.token.source.label": "Token source",
    "settings.data.token.source.session": "custom session token",
    "settings.data.token.source.default": "app default token",
    "settings.data.token.source.none": "none",
    "settings.data.token.client.label": "Client",
    "settings.data.token.client.auto": "auto",
    "settings.data.token.client.read": "trade-api-read",
    "settings.data.token.client.write": "trade-api-write",
    "settings.data.token.default.available": "Default BCS token available in this build.",
    "settings.data.token.note.session": "Token is held in session memory only — it is lost when the app restarts. BCS support is experimental.",
    "settings.data.token.note.moex": "MOEX direct data. No authentication required.",
    "settings.data.token.warn": "Use a read-only BCS token only. Never use a token with trading permissions.",
    "settings.data.fallback": "Fallback to MOEX if BCS fails",
    "settings.data.clear": "Clear",
    "settings.data.test": "Test",
    "settings.data.testing": "Testing…",
    "settings.data.test.ok": "Connection OK",
    "settings.data.save": "Save",
    "settings.data.placeholder.token": "Paste refresh token here…",

    // Settings — live
    "settings.live.feed": "Live Data Feed",
    "settings.live.enable": "Enable live updates",
    "settings.live.status": "Status",
    "settings.live.lastUpdate": "Last update",
    "settings.live.reconnect": "Reconnect",
    "settings.live.reconnecting": "Reconnecting…",
    "settings.live.status.live": "Live",
    "settings.live.status.paused": "Paused",
    "settings.live.status.stale": "Stale",
    "settings.live.status.reconnecting": "Reconnecting…",
    "settings.live.status.error": "Error",
    "settings.live.note": "Poll intervals: quotes every 5 s, portfolio every 15 s, scanner every 60 s. Intervals increase automatically when the app is in the background.",

    // Settings — updates
    "settings.updates.current": "Current version",
    "settings.updates.check": "Check for updates",
    "settings.updates.checking": "Checking…",
    "settings.updates.upToDate": "You are on the latest version",
    "settings.updates.available": "Update available",
    "settings.updates.unsupported": "Your version is no longer supported. Please update.",
    "settings.updates.download": "Download APK",
    "settings.updates.released": "Released",
    "settings.updates.failed": "Update check failed",

    // Settings — AI
    "settings.ai.mode": "AI Panel Mode",
    "settings.ai.mode.mock": "Mock",
    "settings.ai.mode.pa_short": "PA Short",
    "settings.ai.warning": "PA Short mode has not demonstrated consistent profitability in backtesting. It is provided for research purposes only.",
    "settings.ai.descriptions": "Mode descriptions",
    "settings.ai.desc.mock": "Displays static placeholder analysis. No model inference is performed. Safe for UI development and demos.",
    "settings.ai.desc.pa_short": "Local price action model running entirely on-device. Generates short candlestick pattern summaries. No data leaves the device.",
    "settings.ai.note": "AI analysis runs locally on your device. No data is sent to external servers.",

    // Settings — about
    "settings.about.app": "Technical Analyst",
    "settings.about.sources": "Data Sources",
    "settings.about.sources.moex": "MOEX — Moscow Exchange public market data API",
    "settings.about.sources.bcs": "BCS Broker — authenticated market data (experimental)",
    "settings.about.analysis": "AI & Analysis",
    "settings.about.analysis.local": "All AI inference runs locally on-device",
    "settings.about.analysis.noexternal": "No analysis data is sent to external servers",
    "settings.about.analysis.bundled": "Models are bundled with the app",
    "settings.about.warn": "This app does not provide trading recommendations, investment advice, or signals of any kind. All data is for informational purposes only. Trade at your own risk.",
    "settings.about.note": "Technical Analyst is an independent tool and is not affiliated with or endorsed by MOEX, BCS Broker, or any other financial institution.",
    "settings.about.language": "Language",
    "settings.about.language.ru": "Русский",
    "settings.about.language.en": "English",

    // Chart
    "chart.live": "LIVE",
    "chart.paused": "PAUSED",
    "chart.stale": "STALE",
    "chart.reconnecting": "RECONNECTING",
    "chart.error": "ERROR",
    "chart.syncing": "SYNCING",
    "chart.tab.info": "Info",
    "chart.tab.ai": "AI",
    "chart.tab.depth": "Depth",
    "chart.tab.data": "Data",
    "chart.tab.dataHide": "Hide",
    "chart.refresh": "Refresh",
    "chart.loading": "Loading",
    "chart.olderLoading": "Loading older candles…",
    "chart.olderNone": "No older candles",
    "chart.olderError": "Could not load older candles",
    "chart.search.placeholder": "Search ticker…",
    "chart.search.bcsTokenRequired": "BCS token required for GOODS",
    "chart.last": "Last",
    "chart.refreshTime": "Refresh",
    "chart.refreshFailed": "Refresh failed",
    "chart.candles": "candles",
    "chart.nodata": "No candles for",

    // Order book
    "ob.title": "Order Book",
    "ob.bid": "Bid",
    "ob.ask": "Ask",
    "ob.spread": "Spread",
    "ob.mid": "Mid",
    "ob.depth": "Depth",
    "ob.lastUpdate": "Last update",
    "ob.tokenRequired": "BCS token required for order book",
    "ob.loading": "Loading…",
    "ob.selectInstrument": "Select an instrument to view order book.",
    "ob.noData": "No data.",

    // AI panel
    "ai.experimental": "Experimental AI",
    "ai.signal": "AI Signal",
    "ai.short.risk": "SHORT risk",
    "ai.research.only": "Research only",
    "ai.research.warning": "Research only. Backtest has not shown positive net expectancy.",
    "ai.calculating": "Calculating…",
    "ai.notrade": "NO TRADE",
    "ai.confidence.high": "High confidence",
    "ai.confidence.medium": "Medium confidence",
    "ai.confidence.low": "Low confidence",
    "ai.horizon": "Horizon",
    "ai.model": "Model",
    "ai.candles": "candles",
    "ai.disclaimer": "Experimental local signal. Not financial advice.",
  },
};

function readPreferredLang(): Lang {
  try {
    const v = localStorage.getItem(LS_KEY);
    if (v === "ru" || v === "en") return v;
  } catch { /* unavailable */ }
  return DEFAULT_LANG;
}

let _lang: Lang = readPreferredLang();
const _subs = new Set<(lang: Lang) => void>();

export function getLanguage(): Lang {
  return _lang;
}

export function setLanguage(lang: Lang): void {
  if (lang !== "ru" && lang !== "en") return;
  if (lang === _lang) return;
  _lang = lang;
  try { localStorage.setItem(LS_KEY, lang); } catch { /* unavailable */ }
  for (const cb of _subs) cb(lang);
}

export function subscribe(cb: (lang: Lang) => void): () => void {
  _subs.add(cb);
  return () => { _subs.delete(cb); };
}

export function t(key: string): string {
  const dict = STRINGS[_lang] ?? STRINGS[DEFAULT_LANG];
  return dict[key] ?? STRINGS.en[key] ?? key;
}
