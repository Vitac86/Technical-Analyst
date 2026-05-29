import { useState, useEffect } from "react";
import {
  storeRefreshToken,
  clearTokens,
  hasSessionOverride,
  hasDefaultToken,
  getTokenSource,
} from "../../security/tokenStorage";
import type { TokenSource } from "../../security/tokenStorage";
import { testBcsConnection } from "../../api/bcsAuth";
import type { BcsTestResult } from "../../api/bcsAuth";
import { checkForAppUpdate, CURRENT_APP_VERSION_NAME } from "../../api/appUpdate";
import type { AppUpdateManifest } from "../../api/appUpdate";
import { useTranslation } from "../../i18n/useTranslation";
import { setLanguage } from "../../i18n/i18n";
import type { Lang } from "../../i18n/i18n";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SettingsModalProps = {
  open: boolean;
  onClose: () => void;
  providerId: "moex" | "bcs";
  onProviderChange: (id: "moex" | "bcs") => void;
  fallbackEnabled: boolean;
  onFallbackChange: (enabled: boolean) => void;
  liveEnabled: boolean;
  onLiveEnabledChange: (enabled: boolean) => void;
  liveStatus: "live" | "paused" | "stale" | "reconnecting" | "error";
  lastLiveUpdateAt: string | null;
  onReconnect: () => void;
  aiPanelMode: "mock" | "pa_short";
  onAiPanelModeChange: (mode: "mock" | "pa_short") => void;
};

type Tab = "data" | "live" | "updates" | "ai" | "about";

type BcsTestState =
  | { phase: "idle" }
  | { phase: "testing" }
  | { phase: "done"; result: BcsTestResult };

type UpdateCheckState =
  | { phase: "idle" }
  | { phase: "checking" }
  | { phase: "done"; upToDate: true; versionName: string }
  | { phase: "done"; upToDate: false; manifest: AppUpdateManifest; versionName: string }
  | { phase: "done"; unsupported: true; manifest: AppUpdateManifest; versionName: string }
  | { phase: "error"; message: string };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function LiveStatusChip({ status }: { status: SettingsModalProps["liveStatus"] }) {
  const { t } = useTranslation();
  const labelKey: Record<SettingsModalProps["liveStatus"], string> = {
    live: "settings.live.status.live",
    paused: "settings.live.status.paused",
    stale: "settings.live.status.stale",
    reconnecting: "settings.live.status.reconnecting",
    error: "settings.live.status.error",
  };
  return (
    <span className={`mc-sm-status-chip mc-sm-status-chip-${status}`}>
      {t(labelKey[status])}
    </span>
  );
}

function tokenSourceLabel(t: (k: string) => string, src: TokenSource): string {
  if (src === "session") return t("settings.data.token.source.session");
  if (src === "default") return t("settings.data.token.source.default");
  return t("settings.data.token.source.none");
}

// ---------------------------------------------------------------------------
// Tab: Data
// ---------------------------------------------------------------------------

function DataTab({
  providerId,
  onProviderChange,
  fallbackEnabled,
  onFallbackChange,
}: Pick<
  SettingsModalProps,
  "providerId" | "onProviderChange" | "fallbackEnabled" | "onFallbackChange"
>) {
  const { t } = useTranslation();
  const [tokenInput, setTokenInput] = useState("");
  const [sessionSaved, setSessionSaved] = useState(false);
  const [tokenSrc, setTokenSrc] = useState<TokenSource>(getTokenSource());
  const [testState, setTestState] = useState<BcsTestState>({ phase: "idle" });
  const defaultAvailable = hasDefaultToken();

  function refreshTokenStatus() {
    setSessionSaved(hasSessionOverride());
    setTokenSrc(getTokenSource());
  }

  useEffect(() => {
    refreshTokenStatus();
    setTokenInput("");
    setTestState({ phase: "idle" });
  }, [providerId]);

  function handleSaveToken() {
    const trimmed = tokenInput.trim();
    if (!trimmed) return;
    storeRefreshToken(trimmed);
    setTokenInput("");
    refreshTokenStatus();
    setTestState({ phase: "idle" });
  }

  function handleClearToken() {
    clearTokens();
    setTokenInput("");
    refreshTokenStatus();
    setTestState({ phase: "idle" });
  }

  async function handleTest() {
    setTestState({ phase: "testing" });
    const result = await testBcsConnection();
    setTestState({ phase: "done", result });
  }

  return (
    <div className="mc-sm-tab-content">
      {/* Provider selector */}
      <section className="mc-sm-card">
        <div className="mc-sm-card-label">{t("settings.data.source")}</div>
        <div className="mc-sm-segmented">
          <button
            type="button"
            className={`mc-sm-seg-btn${providerId === "moex" ? " mc-sm-seg-btn-active" : ""}`}
            onClick={() => onProviderChange("moex")}
          >
            MOEX
          </button>
          <button
            type="button"
            className={`mc-sm-seg-btn${providerId === "bcs" ? " mc-sm-seg-btn-active" : ""}`}
            onClick={() => onProviderChange("bcs")}
          >
            BCS
          </button>
        </div>
      </section>

      {/* BCS-only section */}
      {providerId === "bcs" && (
        <>
          {defaultAvailable && (
            <section className="mc-sm-card mc-sm-info-card">
              {t("settings.data.token.default.available")}
            </section>
          )}

          <section className="mc-sm-card mc-sm-warn-card">
            {t("settings.data.token.warn")}
          </section>

          <section className="mc-sm-card">
            <div className="mc-sm-card-label">
              {sessionSaved ? t("settings.data.token.saved") : t("settings.data.token.paste")}
            </div>

            <div className="mc-sm-token-source-row">
              <span className="mc-sm-token-source-label">
                {t("settings.data.token.source.label")}:
              </span>
              <span className={`mc-sm-token-source-badge mc-sm-token-source-${tokenSrc}`}>
                {tokenSourceLabel(t, tokenSrc)}
              </span>
            </div>

            {!sessionSaved ? (
              <div className="mc-sm-token-entry">
                <input
                  type="password"
                  className="mc-sm-token-input"
                  placeholder={t("settings.data.placeholder.token")}
                  value={tokenInput}
                  autoComplete="off"
                  autoCorrect="off"
                  spellCheck={false}
                  onChange={(e) => setTokenInput(e.target.value)}
                />
                <button
                  type="button"
                  className="mc-sm-btn mc-sm-btn-primary"
                  disabled={!tokenInput.trim()}
                  onClick={handleSaveToken}
                >
                  {t("settings.data.save")}
                </button>
              </div>
            ) : (
              <div className="mc-sm-token-row">
                <button
                  type="button"
                  className="mc-sm-btn mc-sm-btn-danger"
                  onClick={handleClearToken}
                >
                  {t("settings.data.clear")}
                </button>
                <button
                  type="button"
                  className="mc-sm-btn"
                  disabled={testState.phase === "testing"}
                  onClick={() => { void handleTest(); }}
                >
                  {testState.phase === "testing"
                    ? t("settings.data.testing")
                    : t("settings.data.test")}
                </button>
              </div>
            )}

            {sessionSaved === false && tokenSrc === "default" && (
              <div className="mc-sm-test-row">
                <button
                  type="button"
                  className="mc-sm-btn"
                  disabled={testState.phase === "testing"}
                  onClick={() => { void handleTest(); }}
                >
                  {testState.phase === "testing"
                    ? t("settings.data.testing")
                    : t("settings.data.test")}
                </button>
              </div>
            )}

            {testState.phase === "done" && (
              <div
                className={`mc-sm-test-result${
                  testState.result.ok ? " mc-sm-test-ok" : " mc-sm-test-err"
                }`}
              >
                {testState.result.ok ? t("settings.data.test.ok") : testState.result.message}
              </div>
            )}
          </section>

          {/* Fallback toggle */}
          <section className="mc-sm-card">
            <label className="mc-sm-toggle-row">
              <input
                type="checkbox"
                className="mc-sm-checkbox"
                checked={fallbackEnabled}
                onChange={(e) => onFallbackChange(e.target.checked)}
              />
              <span className="mc-sm-toggle-label">{t("settings.data.fallback")}</span>
            </label>
          </section>
        </>
      )}

      <div className="mc-sm-note">
        {providerId === "bcs"
          ? t("settings.data.token.note.session")
          : t("settings.data.token.note.moex")}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Live
// ---------------------------------------------------------------------------

function LiveTab({
  liveEnabled,
  onLiveEnabledChange,
  liveStatus,
  lastLiveUpdateAt,
  onReconnect,
}: Pick<
  SettingsModalProps,
  "liveEnabled" | "onLiveEnabledChange" | "liveStatus" | "lastLiveUpdateAt" | "onReconnect"
>) {
  const { t } = useTranslation();
  return (
    <div className="mc-sm-tab-content">
      <section className="mc-sm-card">
        <div className="mc-sm-card-label">{t("settings.live.feed")}</div>
        <label className="mc-sm-toggle-row">
          <input
            type="checkbox"
            className="mc-sm-checkbox"
            checked={liveEnabled}
            onChange={(e) => onLiveEnabledChange(e.target.checked)}
          />
          <span className="mc-sm-toggle-label">{t("settings.live.enable")}</span>
        </label>
      </section>

      <section className="mc-sm-card">
        <div className="mc-sm-card-label">{t("settings.live.status")}</div>
        <div className="mc-sm-live-status-row">
          <LiveStatusChip status={liveStatus} />
          {lastLiveUpdateAt && (
            <span className="mc-sm-last-update">
              {t("settings.live.lastUpdate")}: {lastLiveUpdateAt}
            </span>
          )}
        </div>
      </section>

      {liveEnabled && (
        <section className="mc-sm-card">
          <div className="mc-sm-note">
            {t("settings.live.note")}
          </div>
        </section>
      )}

      <section className="mc-sm-card">
        <button
          type="button"
          className="mc-sm-btn mc-sm-btn-primary mc-sm-btn-block"
          disabled={liveStatus === "reconnecting"}
          onClick={onReconnect}
        >
          {liveStatus === "reconnecting"
            ? t("settings.live.reconnecting")
            : t("settings.live.reconnect")}
        </button>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Updates
// ---------------------------------------------------------------------------

function UpdatesTab() {
  const { t } = useTranslation();
  const [checkState, setCheckState] = useState<UpdateCheckState>({ phase: "idle" });

  async function handleCheckUpdate() {
    setCheckState({ phase: "checking" });
    try {
      const result = await checkForAppUpdate();
      if (result.status === "up_to_date") {
        setCheckState({ phase: "done", upToDate: true, versionName: result.currentVersionName });
      } else if (result.status === "update_available") {
        setCheckState({
          phase: "done",
          upToDate: false,
          manifest: result.manifest,
          versionName: result.currentVersionName,
        });
      } else {
        // unsupported
        setCheckState({
          phase: "done",
          unsupported: true,
          manifest: result.manifest,
          versionName: result.currentVersionName,
        } as UpdateCheckState);
      }
    } catch (err) {
      setCheckState({
        phase: "error",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    }
  }

  function handleDownload(apkUrl: string) {
    window.open(apkUrl, "_system");
  }

  return (
    <div className="mc-sm-tab-content">
      <section className="mc-sm-card">
        <div className="mc-sm-card-label">{t("settings.updates.current")}</div>
        <div className="mc-sm-version-display">
          Technical Analyst v{CURRENT_APP_VERSION_NAME}
        </div>
      </section>

      <section className="mc-sm-card">
        <button
          type="button"
          className="mc-sm-btn mc-sm-btn-primary mc-sm-btn-block"
          disabled={checkState.phase === "checking"}
          onClick={() => { void handleCheckUpdate(); }}
        >
          {checkState.phase === "checking"
            ? t("settings.updates.checking")
            : t("settings.updates.check")}
        </button>
      </section>

      {checkState.phase === "done" && "upToDate" in checkState && checkState.upToDate && (
        <div className="mc-sm-update-result mc-sm-update-ok">
          {t("settings.updates.upToDate")} ({checkState.versionName}).
        </div>
      )}

      {checkState.phase === "done" &&
        "upToDate" in checkState &&
        !checkState.upToDate &&
        "unsupported" in checkState &&
        !!(checkState as { unsupported: unknown }).unsupported && (
          <div className="mc-sm-update-result mc-sm-update-warn">
            <p>{t("settings.updates.unsupported")} ({checkState.versionName} → {checkState.manifest.versionName})</p>
            {checkState.manifest.notes && (
              <ul className="mc-sm-release-notes">
                {checkState.manifest.notes.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            )}
            <button
              type="button"
              className="mc-sm-btn mc-sm-btn-primary"
              onClick={() => handleDownload(checkState.manifest.apkUrl)}
            >
              {t("settings.updates.download")} v{checkState.manifest.versionName}
            </button>
          </div>
        )}

      {checkState.phase === "done" &&
        "upToDate" in checkState &&
        !checkState.upToDate &&
        !("unsupported" in checkState && checkState.unsupported) && (
          <div className="mc-sm-update-result mc-sm-update-available">
            <p>
              {t("settings.updates.available")}: v{(checkState as { manifest: AppUpdateManifest }).manifest.versionName}
            </p>
            {(checkState as { manifest: AppUpdateManifest }).manifest.releaseDate && (
              <p className="mc-sm-release-date">
                {t("settings.updates.released")}: {(checkState as { manifest: AppUpdateManifest }).manifest.releaseDate}
              </p>
            )}
            {(checkState as { manifest: AppUpdateManifest }).manifest.notes && (
              <ul className="mc-sm-release-notes">
                {(checkState as { manifest: AppUpdateManifest }).manifest.notes!.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            )}
            <button
              type="button"
              className="mc-sm-btn mc-sm-btn-primary"
              onClick={() =>
                handleDownload((checkState as { manifest: AppUpdateManifest }).manifest.apkUrl)
              }
            >
              {t("settings.updates.download")} v{(checkState as { manifest: AppUpdateManifest }).manifest.versionName}
            </button>
          </div>
        )}

      {checkState.phase === "error" && (
        <div className="mc-sm-update-result mc-sm-update-err">
          {t("settings.updates.failed")}: {checkState.message}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: AI
// ---------------------------------------------------------------------------

function AiTab({
  aiPanelMode,
  onAiPanelModeChange,
}: Pick<SettingsModalProps, "aiPanelMode" | "onAiPanelModeChange">) {
  const { t } = useTranslation();
  return (
    <div className="mc-sm-tab-content">
      <section className="mc-sm-card">
        <div className="mc-sm-card-label">{t("settings.ai.mode")}</div>
        <div className="mc-sm-segmented">
          <button
            type="button"
            className={`mc-sm-seg-btn${aiPanelMode === "mock" ? " mc-sm-seg-btn-active" : ""}`}
            onClick={() => onAiPanelModeChange("mock")}
          >
            {t("settings.ai.mode.mock")}
          </button>
          <button
            type="button"
            className={`mc-sm-seg-btn${aiPanelMode === "pa_short" ? " mc-sm-seg-btn-active" : ""}`}
            onClick={() => onAiPanelModeChange("pa_short")}
          >
            {t("settings.ai.mode.pa_short")}
          </button>
        </div>
      </section>

      <section className="mc-sm-card mc-sm-warn-card">
        {t("settings.ai.warning")}
      </section>

      <section className="mc-sm-card">
        <div className="mc-sm-card-label">{t("settings.ai.descriptions")}</div>
        <div className="mc-sm-ai-desc-list">
          <div className="mc-sm-ai-desc-item">
            <span className="mc-sm-ai-desc-name">{t("settings.ai.mode.mock")}</span>
            <span className="mc-sm-ai-desc-text">{t("settings.ai.desc.mock")}</span>
          </div>
          <div className="mc-sm-ai-desc-item">
            <span className="mc-sm-ai-desc-name">{t("settings.ai.mode.pa_short")}</span>
            <span className="mc-sm-ai-desc-text">{t("settings.ai.desc.pa_short")}</span>
          </div>
        </div>
      </section>

      <div className="mc-sm-note">{t("settings.ai.note")}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: About (includes language selector)
// ---------------------------------------------------------------------------

function AboutTab() {
  const { t, lang } = useTranslation();
  function pickLang(next: Lang) { setLanguage(next); }
  return (
    <div className="mc-sm-tab-content">
      <section className="mc-sm-card mc-sm-about-header">
        <div className="mc-sm-about-name">{t("settings.about.app")}</div>
        <div className="mc-sm-about-version">v{CURRENT_APP_VERSION_NAME}</div>
      </section>

      <section className="mc-sm-card">
        <div className="mc-sm-card-label">{t("settings.about.language")}</div>
        <div className="mc-sm-segmented">
          <button
            type="button"
            className={`mc-sm-seg-btn${lang === "ru" ? " mc-sm-seg-btn-active" : ""}`}
            onClick={() => pickLang("ru")}
          >
            {t("settings.about.language.ru")}
          </button>
          <button
            type="button"
            className={`mc-sm-seg-btn${lang === "en" ? " mc-sm-seg-btn-active" : ""}`}
            onClick={() => pickLang("en")}
          >
            {t("settings.about.language.en")}
          </button>
        </div>
      </section>

      <section className="mc-sm-card">
        <div className="mc-sm-card-label">{t("settings.about.sources")}</div>
        <ul className="mc-sm-about-list">
          <li>{t("settings.about.sources.moex")}</li>
          <li>{t("settings.about.sources.bcs")}</li>
        </ul>
      </section>

      <section className="mc-sm-card">
        <div className="mc-sm-card-label">{t("settings.about.analysis")}</div>
        <ul className="mc-sm-about-list">
          <li>{t("settings.about.analysis.local")}</li>
          <li>{t("settings.about.analysis.noexternal")}</li>
          <li>{t("settings.about.analysis.bundled")}</li>
        </ul>
      </section>

      <section className="mc-sm-card mc-sm-warn-card">
        {t("settings.about.warn")}
      </section>

      <div className="mc-sm-note">{t("settings.about.note")}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SettingsModal({
  open,
  onClose,
  providerId,
  onProviderChange,
  fallbackEnabled,
  onFallbackChange,
  liveEnabled,
  onLiveEnabledChange,
  liveStatus,
  lastLiveUpdateAt,
  onReconnect,
  aiPanelMode,
  onAiPanelModeChange,
}: SettingsModalProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<Tab>("data");

  // Reset to first tab each time modal opens
  useEffect(() => {
    if (open) {
      setActiveTab("data");
    }
  }, [open]);

  if (!open) return null;

  const tabs: Array<{ id: Tab; key: string }> = [
    { id: "data",    key: "settings.tab.data" },
    { id: "live",    key: "settings.tab.live" },
    { id: "updates", key: "settings.tab.updates" },
    { id: "ai",      key: "settings.tab.ai" },
    { id: "about",   key: "settings.tab.about" },
  ];

  return (
    <>
      {/* Overlay */}
      <div
        className="mc-sm-overlay"
        onPointerDown={onClose}
        aria-hidden="true"
      />

      {/* Sheet */}
      <div className="mc-sm-sheet" role="dialog" aria-label={t("settings.title")} aria-modal="true">

        {/* Header */}
        <div className="mc-sm-header">
          <span className="mc-sm-title">{t("settings.title")}</span>
          <button
            type="button"
            className="mc-sm-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {/* Tab bar */}
        <div className="mc-sm-tabbar" role="tablist">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={
                activeTab === tab.id ? "mc-sm-tab-btn mc-sm-tab-btn-active" : "mc-sm-tab-btn"
              }
              onClick={() => setActiveTab(tab.id)}
            >
              {t(tab.key)}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="mc-sm-content">
          {activeTab === "data" && (
            <DataTab
              providerId={providerId}
              onProviderChange={onProviderChange}
              fallbackEnabled={fallbackEnabled}
              onFallbackChange={onFallbackChange}
            />
          )}
          {activeTab === "live" && (
            <LiveTab
              liveEnabled={liveEnabled}
              onLiveEnabledChange={onLiveEnabledChange}
              liveStatus={liveStatus}
              lastLiveUpdateAt={lastLiveUpdateAt}
              onReconnect={onReconnect}
            />
          )}
          {activeTab === "updates" && <UpdatesTab />}
          {activeTab === "ai" && (
            <AiTab
              aiPanelMode={aiPanelMode}
              onAiPanelModeChange={onAiPanelModeChange}
            />
          )}
          {activeTab === "about" && <AboutTab />}
        </div>
      </div>
    </>
  );
}
