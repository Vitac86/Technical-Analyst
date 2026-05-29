import { useState, useEffect } from "react";
import { storeRefreshToken, clearTokens, hasRefreshToken } from "../../security/tokenStorage";
import { testBcsConnection } from "../../api/bcsAuth";
import type { BcsTestResult } from "../../api/bcsAuth";
import { checkForAppUpdate, CURRENT_APP_VERSION_NAME } from "../../api/appUpdate";
import type { AppUpdateManifest } from "../../api/appUpdate";

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
  const labels: Record<SettingsModalProps["liveStatus"], string> = {
    live: "Live",
    paused: "Paused",
    stale: "Stale",
    reconnecting: "Reconnecting…",
    error: "Error",
  };
  return (
    <span className={`mc-sm-status-chip mc-sm-status-chip-${status}`}>
      {labels[status]}
    </span>
  );
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
  const [tokenInput, setTokenInput] = useState("");
  const [tokenSaved, setTokenSaved] = useState(false);
  const [testState, setTestState] = useState<BcsTestState>({ phase: "idle" });

  useEffect(() => {
    setTokenSaved(hasRefreshToken());
    setTokenInput("");
    setTestState({ phase: "idle" });
  }, [providerId]);

  function handleSaveToken() {
    const trimmed = tokenInput.trim();
    if (!trimmed) return;
    storeRefreshToken(trimmed);
    setTokenInput("");
    setTokenSaved(true);
    setTestState({ phase: "idle" });
  }

  function handleClearToken() {
    clearTokens();
    setTokenSaved(false);
    setTokenInput("");
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
      <div className="mc-sm-section">
        <div className="mc-sm-section-label">Data Source</div>
        <div className="mc-sm-choice-row">
          <button
            type="button"
            className={`mc-sm-choice-btn${providerId === "moex" ? " mc-sm-choice-btn-active" : ""}`}
            onClick={() => onProviderChange("moex")}
          >
            MOEX
          </button>
          <button
            type="button"
            className={`mc-sm-choice-btn${providerId === "bcs" ? " mc-sm-choice-btn-active" : ""}`}
            onClick={() => onProviderChange("bcs")}
          >
            BCS
          </button>
        </div>
      </div>

      {/* BCS-only section */}
      {providerId === "bcs" && (
        <>
          <div className="mc-sm-warn">
            Use a read-only BCS token only. Never use a token with trading permissions.
          </div>

          <div className="mc-sm-section">
            <div className="mc-sm-section-label">
              {tokenSaved ? "Token status" : "Paste BCS refresh token"}
            </div>

            {!tokenSaved ? (
              <div className="mc-sm-token-entry">
                <input
                  type="password"
                  className="mc-sm-token-input"
                  placeholder="Paste refresh token here…"
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
                  Save
                </button>
              </div>
            ) : (
              <div className="mc-sm-token-row">
                <span className="mc-sm-token-saved-label">BCS token saved</span>
                <button
                  type="button"
                  className="mc-sm-btn mc-sm-btn-ghost"
                  onClick={handleClearToken}
                >
                  Clear
                </button>
                <button
                  type="button"
                  className="mc-sm-btn"
                  disabled={testState.phase === "testing"}
                  onClick={() => { void handleTest(); }}
                >
                  {testState.phase === "testing" ? "Testing…" : "Test"}
                </button>
              </div>
            )}

            {testState.phase === "done" && (
              <div
                className={`mc-sm-test-result${
                  testState.result.ok ? " mc-sm-test-ok" : " mc-sm-test-err"
                }`}
              >
                {testState.result.ok ? "Connection OK" : testState.result.message}
              </div>
            )}
          </div>

          {/* Fallback toggle */}
          <div className="mc-sm-section">
            <label className="mc-sm-toggle-row">
              <input
                type="checkbox"
                className="mc-sm-checkbox"
                checked={fallbackEnabled}
                onChange={(e) => onFallbackChange(e.target.checked)}
              />
              <span className="mc-sm-toggle-label">Fallback to MOEX if BCS fails</span>
            </label>
          </div>
        </>
      )}

      <div className="mc-sm-note">
        {providerId === "bcs"
          ? "Token is held in session memory only — it is lost when the app restarts or the page reloads. BCS support is experimental."
          : "MOEX direct data. No authentication required."}
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
  return (
    <div className="mc-sm-tab-content">
      <div className="mc-sm-section">
        <div className="mc-sm-section-label">Live Data Feed</div>
        <label className="mc-sm-toggle-row">
          <input
            type="checkbox"
            className="mc-sm-checkbox"
            checked={liveEnabled}
            onChange={(e) => onLiveEnabledChange(e.target.checked)}
          />
          <span className="mc-sm-toggle-label">Enable live updates</span>
        </label>
      </div>

      <div className="mc-sm-section">
        <div className="mc-sm-section-label">Status</div>
        <div className="mc-sm-live-status-row">
          <LiveStatusChip status={liveStatus} />
          {lastLiveUpdateAt && (
            <span className="mc-sm-last-update">
              Last update: {lastLiveUpdateAt}
            </span>
          )}
        </div>
      </div>

      {liveEnabled && (
        <div className="mc-sm-section">
          <div className="mc-sm-note">
            Poll intervals: quotes every 5 s, portfolio every 15 s, scanner every 60 s.
            Intervals increase automatically when the app is in the background.
          </div>
        </div>
      )}

      <div className="mc-sm-section">
        <button
          type="button"
          className="mc-sm-btn mc-sm-btn-primary"
          disabled={liveStatus === "reconnecting"}
          onClick={onReconnect}
        >
          {liveStatus === "reconnecting" ? "Reconnecting…" : "Reconnect"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Updates
// ---------------------------------------------------------------------------

function UpdatesTab() {
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
      <div className="mc-sm-section">
        <div className="mc-sm-section-label">Current version</div>
        <div className="mc-sm-version-display">
          Technical Analyst v{CURRENT_APP_VERSION_NAME}
        </div>
      </div>

      <div className="mc-sm-section">
        <button
          type="button"
          className="mc-sm-btn mc-sm-btn-primary"
          disabled={checkState.phase === "checking"}
          onClick={() => { void handleCheckUpdate(); }}
        >
          {checkState.phase === "checking" ? "Checking…" : "Check for updates"}
        </button>
      </div>

      {checkState.phase === "done" && "upToDate" in checkState && checkState.upToDate && (
        <div className="mc-sm-update-result mc-sm-update-ok">
          You are on the latest version ({checkState.versionName}).
        </div>
      )}

      {checkState.phase === "done" &&
        "upToDate" in checkState &&
        !checkState.upToDate &&
        "unsupported" in checkState &&
        !!(checkState as { unsupported: unknown }).unsupported && (
          <div className="mc-sm-update-result mc-sm-update-warn">
            <p>
              Your version ({checkState.versionName}) is no longer supported. Please update to{" "}
              {checkState.manifest.versionName}.
            </p>
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
              Download APK v{checkState.manifest.versionName}
            </button>
          </div>
        )}

      {checkState.phase === "done" &&
        "upToDate" in checkState &&
        !checkState.upToDate &&
        !("unsupported" in checkState && checkState.unsupported) && (
          <div className="mc-sm-update-result mc-sm-update-available">
            <p>
              Update available: v{(checkState as { manifest: AppUpdateManifest }).manifest.versionName}
              {" "}(current: {checkState.versionName})
            </p>
            {(checkState as { manifest: AppUpdateManifest }).manifest.releaseDate && (
              <p className="mc-sm-release-date">
                Released: {(checkState as { manifest: AppUpdateManifest }).manifest.releaseDate}
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
              Download APK v{(checkState as { manifest: AppUpdateManifest }).manifest.versionName}
            </button>
          </div>
        )}

      {checkState.phase === "error" && (
        <div className="mc-sm-update-result mc-sm-update-err">
          Update check failed: {checkState.message}
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
  return (
    <div className="mc-sm-tab-content">
      <div className="mc-sm-section">
        <div className="mc-sm-section-label">AI Panel Mode</div>
        <div className="mc-sm-choice-row">
          <button
            type="button"
            className={`mc-sm-choice-btn${aiPanelMode === "mock" ? " mc-sm-choice-btn-active" : ""}`}
            onClick={() => onAiPanelModeChange("mock")}
          >
            Mock
          </button>
          <button
            type="button"
            className={`mc-sm-choice-btn${aiPanelMode === "pa_short" ? " mc-sm-choice-btn-active" : ""}`}
            onClick={() => onAiPanelModeChange("pa_short")}
          >
            PA Short
          </button>
        </div>
      </div>

      <div className="mc-sm-warn">
        PA Short mode has not demonstrated consistent profitability in backtesting.
        It is provided for research purposes only.
      </div>

      <div className="mc-sm-section">
        <div className="mc-sm-section-label">Mode descriptions</div>
        <div className="mc-sm-ai-desc-list">
          <div className="mc-sm-ai-desc-item">
            <span className="mc-sm-ai-desc-name">Mock</span>
            <span className="mc-sm-ai-desc-text">
              Displays static placeholder analysis. No model inference is performed.
              Safe for UI development and demos.
            </span>
          </div>
          <div className="mc-sm-ai-desc-item">
            <span className="mc-sm-ai-desc-name">PA Short</span>
            <span className="mc-sm-ai-desc-text">
              Local price action model running entirely on-device. Generates short
              candlestick pattern summaries. No data leaves the device.
            </span>
          </div>
        </div>
      </div>

      <div className="mc-sm-note">
        AI analysis runs locally on your device. No data is sent to external servers.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: About
// ---------------------------------------------------------------------------

function AboutTab() {
  return (
    <div className="mc-sm-tab-content">
      <div className="mc-sm-section mc-sm-about-header">
        <div className="mc-sm-about-name">Technical Analyst</div>
        <div className="mc-sm-about-version">v{CURRENT_APP_VERSION_NAME}</div>
      </div>

      <div className="mc-sm-section">
        <div className="mc-sm-section-label">Data Sources</div>
        <ul className="mc-sm-about-list">
          <li>MOEX — Moscow Exchange public market data API</li>
          <li>BCS Broker — authenticated portfolio and order data (experimental)</li>
        </ul>
      </div>

      <div className="mc-sm-section">
        <div className="mc-sm-section-label">AI &amp; Analysis</div>
        <ul className="mc-sm-about-list">
          <li>All AI inference runs locally on-device</li>
          <li>No analysis data is sent to external servers</li>
          <li>Models are bundled with the app</li>
        </ul>
      </div>

      <div className="mc-sm-warn">
        This app does not provide trading recommendations, investment advice, or
        signals of any kind. All data is for informational purposes only.
        Trade at your own risk.
      </div>

      <div className="mc-sm-note">
        Technical Analyst is an independent tool and is not affiliated with or
        endorsed by MOEX, BCS Broker, or any other financial institution.
      </div>
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
  const [activeTab, setActiveTab] = useState<Tab>("data");

  // Reset to first tab each time modal opens
  useEffect(() => {
    if (open) {
      setActiveTab("data");
    }
  }, [open]);

  if (!open) return null;

  const tabs: Array<{ id: Tab; label: string }> = [
    { id: "data", label: "Data" },
    { id: "live", label: "Live" },
    { id: "updates", label: "Updates" },
    { id: "ai", label: "AI" },
    { id: "about", label: "About" },
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
      <div className="mc-sm-sheet" role="dialog" aria-label="Settings" aria-modal="true">

        {/* Header */}
        <div className="mc-sm-header">
          <span className="mc-sm-title">Settings</span>
          <button
            type="button"
            className="mc-sm-close"
            onClick={onClose}
            aria-label="Close settings"
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
              {tab.label}
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
