export const CURRENT_APP_VERSION_CODE = 9;
export const CURRENT_APP_VERSION_NAME = "1.0.8";

const UPDATE_MANIFEST_URL =
  "https://raw.githubusercontent.com/Vitac86/Technical-Analyst/main/mobile-update.json";

export type AppUpdateManifest = {
  versionCode: number;
  versionName: string;
  apkUrl: string;
  releaseDate?: string;
  notes?: string[];
  minSupportedVersionCode?: number;
};

export type AppUpdateCheckResult =
  | { status: "up_to_date"; currentVersionName: string }
  | { status: "update_available"; currentVersionName: string; manifest: AppUpdateManifest }
  | { status: "unsupported"; currentVersionName: string; manifest: AppUpdateManifest };

export async function checkForAppUpdate(): Promise<AppUpdateCheckResult> {
  const res = await fetch(`${UPDATE_MANIFEST_URL}?t=${Date.now()}`);
  if (!res.ok) throw new Error(`Update check failed: HTTP ${res.status}`);

  const data: unknown = await res.json();

  if (
    typeof data !== "object" ||
    data === null ||
    typeof (data as Record<string, unknown>).versionCode !== "number" ||
    typeof (data as Record<string, unknown>).versionName !== "string" ||
    typeof (data as Record<string, unknown>).apkUrl !== "string"
  ) {
    throw new Error("Invalid update manifest");
  }

  const manifest = data as AppUpdateManifest;

  if (
    typeof manifest.minSupportedVersionCode === "number" &&
    CURRENT_APP_VERSION_CODE < manifest.minSupportedVersionCode
  ) {
    return { status: "unsupported", currentVersionName: CURRENT_APP_VERSION_NAME, manifest };
  }

  if (manifest.versionCode > CURRENT_APP_VERSION_CODE) {
    return { status: "update_available", currentVersionName: CURRENT_APP_VERSION_NAME, manifest };
  }

  return { status: "up_to_date", currentVersionName: CURRENT_APP_VERSION_NAME };
}
