import { useEffect, useState } from "react";
import { getLanguage, subscribe, t as translate } from "./i18n";
import type { Lang } from "./i18n";

/**
 * Subscribe a component to language changes. Returns a stable `t` reference
 * and the current language. Components re-render when the language switches.
 */
export function useTranslation(): { t: (key: string) => string; lang: Lang } {
  const [lang, setLang] = useState<Lang>(getLanguage);
  useEffect(() => {
    return subscribe((next) => setLang(next));
  }, []);
  return { t: translate, lang };
}
